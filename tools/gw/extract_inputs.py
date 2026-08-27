#!/usr/bin/env python3
"""Reads each handheld's button wiring out of MAME's hh_sm510.cpp.

The driver states it exactly once, in three places that have to be read
together, so this reads all three rather than having anyone copy them
into a table by hand:

  INPUT_PORTS_START(x)  the ports. Each PORT_START("IN.n") is one input
                        column; each PORT_BIT gives a mask and what it
                        is. "BA" and "B" are the chip's two test pins,
                        which some games wire to a joystick and others
                        leave as unpopulated cheat jumpers.
  inp_fixed_last()      when a game calls it, the LAST IN.n is not a mux
                        column at all: it is tied on and always read.
  read_inputs()         K = the OR of the selected columns, plus the
                        fixed one if there is a fixed one.

Which signal does the selecting depends on the chip. SM510 and SM511
strobe with S. SM5A has no S: the driver wires piezo_input_w, so R bit 0
drives the piezo and R bits 1 and up are the mux. Same read either way.

Emits JSON keyed by romset. Run:
  python3 tools/gw/extract_inputs.py <hh_sm510.cpp> <out.json>
"""
import json, os, re, sys

# MAME's input ids, mapped to the action names the game templates use.
IPT_ACTION = {
    'SELECT': 'time', 'START2': 'gameb', 'START1': 'gamea',
    'SERVICE2': 'alarm', 'SERVICE1': 'acl', 'START': 'start',
    'JOYSTICK_UP': 'up', 'JOYSTICK_DOWN': 'down',
    'JOYSTICK_LEFT': 'left', 'JOYSTICK_RIGHT': 'right',
    'JOYSTICKLEFT_UP': 'lup', 'JOYSTICKLEFT_DOWN': 'ldown',
    'JOYSTICKLEFT_LEFT': 'lleft', 'JOYSTICKLEFT_RIGHT': 'lright',
    'JOYSTICKRIGHT_UP': 'rup', 'JOYSTICKRIGHT_DOWN': 'rdown',
    'JOYSTICKRIGHT_LEFT': 'rleft', 'JOYSTICKRIGHT_RIGHT': 'rright',
    'BUTTON1': 'b1', 'BUTTON2': 'b2', 'BUTTON3': 'b3',
}
# A label beats the generic id: the driver labels the odd ones, either
# with PORT_NAME or a trailing comment, and those labels are the names
# the buttons carry on the actual unit.
NAME_ACTION = {
    'time': 'time', 'game b': 'gameb', 'game a': 'gamea', 'alarm': 'alarm',
    'acl': 'acl', 'hit': 'hit', 'jump': 'jump', 'punch': 'punch',
    'fire': 'fire', 'shoot': 'shoot', 'left': 'left', 'right': 'right',
    'up': 'up', 'down': 'down',
    'power on/start': 'start', 'on/start': 'start', 'start': 'start',
    'pause': 'pause', 'sound': 'sound', 'mode': 'time', 'score': 'time',
    'select': 'select',
}

# Every per-chip machine-config helper the driver offers, not just the
# plain ones: the multi-screen units call sm510_dualv, sm511_tripleh and
# friends, and the Tiger handhelds call sm510_tiger, which is how 73 of
# the 144 games first came back with no CPU at all.
CPU_CALL = re.compile(r'\b(?:mcfg_cpu_)?(sm5[0-9a-z]+?)_(?:common|dualv|dualh|'
                      r'tripleh|triplev|tiger\w*|rotated)\b|\bmcfg_cpu_(sm5[0-9a-z]+)\b')
SCREENS = {'dualv': 2, 'dualh': 2, 'tripleh': 3, 'triplev': 3}


def _body_after(src, pos):
    """The brace-balanced block starting at the '{' at or after pos."""
    i = src.index('{', pos) + 1
    depth = 1
    start = i
    while depth and i < len(src):
        if src[i] == '{': depth += 1
        elif src[i] == '}': depth -= 1
        i += 1
    return src[start:i - 1]


def parse(src):
    # machine-config bodies, to learn each game's CPU
    cfg, nscreens = {}, {}
    for m in re.finditer(r'void\s+(\w+)_state::(\w+)\s*\(machine_config\s*&\s*config\)', src):
        body = _body_after(src, m.end())
        hit = CPU_CALL.search(body)
        if hit:
            cfg[m.group(2)] = hit.group(1) or hit.group(2)
            kind = re.search(r'_(dualv|dualh|tripleh|triplev)\b', hit.group(0))
            nscreens[m.group(2)] = SCREENS.get(kind.group(1), 1) if kind else 1
        else:
            m2 = re.search(r'(?:(\w+)_state::)?(\w+)\(config\)', body)
            cfg[m.group(2)] = ('->', m2.group(2)) if m2 else None

    def cpu_of(name, seen=()):
        v = cfg.get(name)
        if isinstance(v, tuple) and name not in seen:
            return cpu_of(v[1], seen + (name,))
        return v

    def screens_of(name, seen=()):
        v = cfg.get(name)
        if isinstance(v, tuple) and name not in seen:
            return screens_of(v[1], seen + (name,))
        return nscreens.get(name, 1)

    # per-game source regions, to spot inp_fixed_last()
    marks = [(m.start(), m.group(1)) for m in
             re.finditer(r'class (\w+)_state : public hh_sm510_state', src)]
    marks.append((len(src), None))
    region = {marks[i][1]: src[marks[i][0]:marks[i + 1][0]] for i in range(len(marks) - 1)}

    ports = {m.group(1): m.group(2) for m in re.finditer(
        r'static INPUT_PORTS_START\(\s*(\w+)\s*\)(.*?)INPUT_PORTS_END', src, re.S)}

    def resolve_ports(name, seen=()):
        """PORT_INCLUDE pulls in another block; PORT_MODIFY then replaces
        one named port inside it (trobhood does both)."""
        body = ports.get(name)
        if body is None or name in seen:
            return body
        inc = re.search(r'PORT_INCLUDE\(\s*(\w+)\s*\)', body)
        if not inc:
            return body
        base = resolve_ports(inc.group(1), seen + (name,)) or ''
        for mm in re.finditer(r'PORT_MODIFY\("([^"]+)"\)(.*?)(?=PORT_MODIFY\(|$)',
                              body[inc.end():], re.S):
            tag, repl = mm.group(1), mm.group(2)
            base = re.sub(r'(PORT_START\("%s"\)).*?(?=PORT_START\(|$)' % re.escape(tag),
                          lambda m: m.group(1) + repl, base, flags=re.S)
        return base

    out = {}
    for m in re.finditer(r'^(?:SYST|CONS|COMP|GAME)\(\s*[\d?]{4}\s*,\s*(\w+)\s*,'
                         r'\s*\w+\s*,\s*\w+\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)_state\s*,'
                         r'\s*\w+\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"', src, re.M):
        rom, machine, inp, cls, maker, title = m.groups()
        body = resolve_ports(inp)
        if body is None:
            continue
        cols, pins, order, labels = {}, {}, [], {}
        cur = None
        for line in body.splitlines():
            ms = re.search(r'PORT_START\("([^"]+)"\)', line)
            if ms:
                cur = ms.group(1)
                if cur.startswith('IN.'):
                    order.append(cur); cols.setdefault(cur, {})
                continue
            mb = re.search(r'PORT_BIT\(\s*(0x[0-9a-fA-F]+),\s*IP_ACTIVE_(\w+),\s*IPT_(\w+)', line)
            if not mb or cur is None:
                continue
            mask, active, ipt = int(mb.group(1), 16), mb.group(2), mb.group(3)
            if ipt == 'UNUSED':
                continue
            label = re.search(r'PORT_NAME\("([^"]+)"\)', line)
            comment = re.search(r'//\s*([A-Za-z][A-Za-z0-9 ]*)$', line.strip())
            act = None
            for text in (label.group(1) if label else None,
                         comment.group(1).strip() if comment else None):
                if text and text.lower() in NAME_ACTION:
                    act = NAME_ACTION[text.lower()]; break
            if act is None:
                act = IPT_ACTION.get(ipt, ipt.lower())
            if act == 'acl':
                continue                      # reset pin, not a K input
            if cur.startswith('IN.'):
                cols[cur][act] = mask
                if label:
                    labels.setdefault(cur, {})[mask] = label.group(1)
                elif comment:
                    labels.setdefault(cur, {})[mask] = comment.group(1).strip()
            elif cur in ('BA', 'B'):
                # only a real input; the cheat jumpers are PORT_CONFNAME
                pins[cur.lower()] = {'action': act, 'active_low': active == 'LOW'}
        fixed_last = 'inp_fixed_last()' in region.get(machine, '')
        ncols = len(order) - 1 if (fixed_last and order) else len(order)
        out[rom] = {
            'cpu': cpu_of(machine), 'screens': screens_of(machine),
            'title': title, 'maker': maker,
            'columns': [cols[c] for c in order],   # IN.0 first
            'labels': [labels.get(c, {}) for c in order],
            'mux_columns': ncols,                  # the rest are strobed
            'fixed_column': (len(order) - 1) if fixed_last and order else None,
            'pins': pins,
        }
    return out


def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else 'sm510/ref/hh_sm510.cpp'
    out_path = sys.argv[2] if len(sys.argv) > 2 else 'tools/gw/inputs.json'
    data = parse(open(src_path, errors='replace').read())
    json.dump(data, open(out_path, 'w'), indent=1, sort_keys=True)
    print('wrote %s (%d games)' % (out_path, len(data)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
