-- Sharp SM5A core in Lua.
--
-- A port of MAME's implementation (src/devices/cpu/sm510: sm510base,
-- sm500, sm5a; BSD-3-Clause, copyright-holders hap, thanks-to Igor),
-- restructured as a single self-contained module with no host
-- dependencies. Behavioural reference is that code, not the datasheet.
--
-- The SM5A is the chip inside the early silver/gold Nintendo Game &
-- Watch units (Ball is AC-01). 4-bit accumulator, 1.8KB ROM, 5x13x4
-- RAM, one stack level, a 15-bit divider off the 32768Hz crystal, and
-- an LCD driven from two banks of nine 4-bit shift latches (W and W').
--
-- Usage:
--   local sm5a = require 'sm5a'
--   local cpu = sm5a.new{
--     rom = <string of 0x740 bytes>,
--     read_k  = function() return k end,     -- 4-bit, pull-down (0 idle)
--     read_ba = function() return 1 end,     -- 1-bit, pull-up (1 idle)
--     read_b  = function() return 1 end,     -- 1-bit, pull-up (1 idle)
--     write_r = function(r) end,             -- buzzer, R1 active low
--     r_mask_option = 'direct',              -- or a divider bit number
--   }
--   cpu:reset()
--   cpu:run(cycles)      -- machine cycles; 16384 per second of game time
--   cpu:segments()       -- -> { [o*2+h] = nibble }, o=0..8, h=0..1
--
-- Timing model: one machine cycle = 2 crystal ticks. The divider
-- advances inline with execution rather than on an async timer, which
-- keeps the core deterministic; MAME's timer phase can differ from this
-- by a fraction of a cycle around divider boundaries.

local M = {}

local O_PINS = 9        -- SM5A drives 9 O groups
local PRGMASK = 0x7ff
local PAGEMASK = 0x3f

-- default digit-segments PLA (sm500op.cpp get_digit)
local LUT_DIGITS = {
  [0]=0xe,0x0,0xc,0x8,0x2,0xa,0xe,0x2,0xe,0xa,0x0,0x0,0x2,0xa,0x2,0x2,
      0xb,0x9,0x7,0xf,0xd,0xe,0xe,0xb,0xf,0xf,0x4,0x0,0xd,0xe,0x4,0x0
}

local cpu = {}
cpu.__index = cpu

function M.new(opts)
  local self = setmetatable({}, cpu)

  -- program: 0x000-0x6ff direct, 0x700-0x73f mirrored across 0x740-0x7ff
  local rom = opts.rom
  assert(#rom >= 0x740, 'SM5A ROM must be 0x740 bytes')
  self.prg = {}
  for a = 0, PRGMASK do
    local src = a
    if a >= 0x700 then src = 0x700 + (a & 0x3f) end
    self.prg[a] = rom:byte(src + 1)
  end

  self.read_k  = opts.read_k  or function() return 0 end
  self.read_ba = opts.read_ba or function() return 1 end
  self.read_b  = opts.read_b  or function() return 1 end
  self.write_r = opts.write_r or function() end
  self.r_mask_option = opts.r_mask_option or 'direct'

  -- 5 rows of 13 nibbles; BM 5,6,7 mirror row 4, BL 13,14,15 mirror 12
  self.ram = {}
  for i = 0, 0x4c do self.ram[i] = 0 end

  self.stack = 0
  return self
end

-- effective RAM address with the SM5A mirrors folded in
local function ram_addr(self)
  local bm, bl = self.bm & 7, self.bl
  if bm > 4 then bm = 4 end
  if bl > 0xc then bl = 0xc end
  return bm << 4 | bl
end

local function ram_r(self) return self.ram[ram_addr(self)] end
local function ram_w(self, v) self.ram[ram_addr(self)] = v & 0xf end

local function bitmask(op) return 1 << (op & 3) end

-- PC low 6 bits are an LFSR: new msb = ~(bit0 ~ bit1)
local function increment_pc(self)
  local pc = self.pc
  local feed = (((pc >> 1 ~ pc) & 1) == 1) and 0 or 0x20
  self.pc = feed | (pc >> 1 & 0x1f) | (pc & ~PAGEMASK & PRGMASK)
end

local function do_branch(self, pu, pm, pl)
  self.pc = ((pu << 10) | (pm << 6 & 0x3c0) | (pl & 0x3f)) & PRGMASK
end

local function push_stack(self) self.stack = self.pc end
local function pop_stack(self) self.pc = self.stack & PRGMASK end

-- SSR stashes the next call's page in the stack's upper bits
local function set_su(self, su)
  self.stack = (self.stack & ~0x3c0) | (su << 6 & 0x3c0)
end
local function get_su(self) return self.stack >> 6 & 0xf end

local function get_digit(self)
  local sel = self.bp >> 3 & 1
  local d = LUT_DIGITS[sel << 4 | self.acc]
  if sel == 0 then d = d | self.mx end
  return d
end

local function shift_w(self)
  for i = 0, O_PINS - 2 do self.ox[i] = self.ox[i + 1] end
end

-- buzzer: R1 from divider or direct control, active low (sm500.cpp)
local function clock_melody(self)
  local mask
  if self.r_mask_option == 'direct' then mask = 1
  else mask = self.div >> self.r_mask_option & 1 end
  local nr = ~self.r & 0xf
  local out = (mask & nr) | (nr & 0xe)
  if out ~= self.r_out then
    self.r_out = out
    self.write_r(out)
  end
end

-- advance the divider n crystal ticks (2 per machine cycle)
local function clock_div(self, n)
  for _ = 1, n do
    self.div = (self.div + 1) & 0x7fff
    if self.div == 0 then self.gamma = 1 end
    clock_melody(self)
  end
end

function cpu:reset()
  self.pc, self.prev_pc = 0, 0
  self.op, self.prev_op, self.param = 0, 0, 0
  self.acc, self.bl, self.bm, self.c = 0, 0, 0, 0
  self.skip, self.halt = false, false
  self.w = 0
  self.div, self.gamma = 0, 0
  self.l, self.x, self.y = 0, 0, 0
  self.bc = false
  self.r, self.r_out = 0, 0

  self.o, self.ox = {}, {}
  for i = 0, O_PINS - 1 do self.o[i] = 0; self.ox[i] = 0 end
  self.mx, self.cb, self.s = 0, 0, 0
  self.rsub = false

  -- base reset: vector page 0xf, LCD on
  do_branch(self, 0, 0xf, 0)
  self.prev_pc = self.pc
  self.bp = 1
  self.write_r(0)

  -- SM500-family reset on top of that
  push_stack(self)
  self.div = self.div & 0x3f      -- IDIV
  self.gamma = 1
  self.cb = 0
  self.rsub = false
  self.r = 0xf
end

-- opcode implementations ----------------------------------------------------

local function op_lb(self)   -- SM500 variant
  local op = self.op
  self.bm = op & 3
  self.bl = (op >> 2 & 3) | (((op & 0xc) ~= 0) and 8 or 0)
end

local function op_incb(self)
  self.bl = (self.bl + 1) & 0xf
  self.skip = (self.bl == 8)   -- SM500: overflow on 3rd bit
end

local function op_decb(self)
  self.bl = (self.bl - 1) & 0xf
  self.skip = (self.bl == 0xf)
end

local function op_tr(self)
  local op = self.op
  self.pc = (self.pc & ~0x3f & PRGMASK) | (op & 0x3f)
  if not self.rsub then
    do_branch(self, self.cb, get_su(self), self.pc & 0x3f)
  end
end

local function op_trs(self)
  local op = self.op
  if not self.rsub then
    self.rsub = true
    local su = get_su(self)
    push_stack(self)
    do_branch(self, 1, 0, op & 0x3f)   -- SM5A: TRS field 1
    if (self.prev_op & 0xf0) == 0x70 then  -- E flag from SSR
      do_branch(self, self.cb, su, self.pc & 0x3f)
    end
  else
    self.pc = (self.pc & ~0xff & PRGMASK) | (op << 2 & 0xc0) | (op & 0xf)
  end
end

local function op_exc(self)
  local a = self.acc
  self.acc = ram_r(self)
  ram_w(self, a)
  self.bm = self.bm ~ (self.op & 3)
end

local function op_lda(self)
  self.acc = ram_r(self)
  self.bm = self.bm ~ (self.op & 3)
end

local function op_lax(self)
  if (self.op & ~0xf) ~= (self.prev_op & ~0xf) then
    self.acc = self.op & 0xf
  end
end

local function op_adx(self)
  local x = self.op & 0xf
  self.acc = self.acc + x
  self.skip = (x ~= 10) and ((self.acc & 0x10) ~= 0)
  self.acc = self.acc & 0xf
end

local function op_add11(self)
  self.acc = self.acc + ram_r(self) + self.c
  self.c = self.acc >> 4 & 1
  self.skip = (self.c == 1)
  self.acc = self.acc & 0xf
end

local function op_dtw(self)
  shift_w(self)
  self.ox[O_PINS - 1] = get_digit(self)
end

local function op_pdtw(self)
  self.ox[O_PINS - 2] = self.ox[O_PINS - 1]
  self.ox[O_PINS - 1] = get_digit(self)
end

local function op_wr(self)
  shift_w(self)
  self.ox[O_PINS - 1] = self.acc & 7
end

local function op_ws(self)
  shift_w(self)
  self.ox[O_PINS - 1] = self.acc | 8
end

local function op_tw(self)
  for i = 0, O_PINS - 1 do self.o[i] = self.ox[i] end
end

local function op_ptw(self)
  self.o[O_PINS - 1] = self.ox[O_PINS - 1]
  self.o[O_PINS - 2] = self.ox[O_PINS - 2]
end

-- dispatch (sm5a.cpp execute_one) -------------------------------------------

local function execute_one(self)
  local op = self.op
  local hi = op & 0xf0

  if hi == 0x20 then op_lax(self)
  elseif hi == 0x30 then op_adx(self)
  elseif hi == 0x40 then op_lb(self)
  elseif hi == 0x70 then set_su(self, op & 0xf)               -- SSR
  elseif hi >= 0x80 and hi <= 0xb0 then op_tr(self)
  elseif hi >= 0xc0 then op_trs(self)
  else
    local q = op & 0xfc
    if     q == 0x04 then ram_w(self, ram_r(self) & ~bitmask(op))  -- RM
    elseif q == 0x0c then ram_w(self, ram_r(self) | bitmask(op))   -- SM
    elseif q == 0x10 then op_exc(self)
    elseif q == 0x14 then op_exc(self); op_incb(self)              -- EXCI
    elseif q == 0x18 then op_lda(self)
    elseif q == 0x1c then op_exc(self); op_decb(self)              -- EXCD
    elseif q == 0x54 then self.skip = (ram_r(self) & bitmask(op)) ~= 0  -- TM
    elseif op == 0x00 then                                          -- SKIP
    elseif op == 0x01 then self.r = self.acc; clock_melody(self)    -- ATR
    elseif op == 0x02 then self.bm = self.bm | 4                    -- SBM
    elseif op == 0x03 then self.bp = self.acc                       -- ATBP
    elseif op == 0x08 then self.acc = (self.acc + ram_r(self)) & 0xf -- ADD
    elseif op == 0x09 then op_add11(self)                           -- ADDC
    elseif op == 0x0a then self.acc = self.acc ~ 0xf                -- COMA
    elseif op == 0x0b then self.acc, self.bl = self.bl, self.acc    -- EXBLA
    elseif op == 0x50 then self.skip = (self.read_ba() ~= 0)        -- TA
    elseif op == 0x51 then self.skip = (self.read_b() ~= 0)         -- TB
    elseif op == 0x52 then self.skip = (self.c == 0)                -- TC
    elseif op == 0x53 then self.skip = (self.acc == ram_r(self))    -- TAM
    elseif op == 0x58 then self.skip = (self.gamma == 0); self.gamma = 0 -- TG
    elseif op == 0x59 then op_ptw(self)
    elseif op == 0x5a then self.skip = (self.acc == 0)              -- TA0
    elseif op == 0x5b then self.skip = (self.acc == self.bl)        -- TABL
    elseif op == 0x5c then op_tw(self)
    elseif op == 0x5d then op_dtw(self)
    elseif op == 0x5f then                                          -- LBL
      self.bl = self.param & 0xf
      self.bm = (self.param & 0x7f) >> 4
    elseif op == 0x60 then self.bp = self.bp ~ 8                    -- COMCN
    elseif op == 0x61 then op_pdtw(self)
    elseif op == 0x62 then op_wr(self)
    elseif op == 0x63 then op_ws(self)
    elseif op == 0x64 then op_incb(self)
    elseif op == 0x65 then self.div = self.div & 0x3f               -- IDIV
    elseif op == 0x66 then self.c = 0                               -- RC
    elseif op == 0x67 then self.c = 1                               -- SC
    elseif op == 0x68 then self.mx = 0; self.acc = 0                -- RMF
    elseif op == 0x69 then self.mx = 1                              -- SMF
    elseif op == 0x6a then self.acc = self.read_k() & 0xf           -- KTA
    elseif op == 0x6b then self.bm = self.bm & ~4                   -- RBM
    elseif op == 0x6c then op_decb(self)
    elseif op == 0x6d then self.cb = self.cb ~ 1                    -- COMCB
    elseif op == 0x6e then pop_stack(self); self.rsub = false       -- RTN
    elseif op == 0x6f then                                          -- RTNS
      pop_stack(self); self.rsub = false; self.skip = true
    elseif op == 0x5e then
      local p = self.param
      self.op = op << 8 | p
      if p == 0x00 then self.halt = true                            -- CEND
      elseif p == 0x04 then self.acc = self.div >> 11 & 0xf         -- DTA
      end
    end
  end
end

-- run n machine cycles ------------------------------------------------------

function cpu:run(n)
  local left = n
  while left > 0 do
    if self.halt then
      -- wake on gamma or any K input; otherwise idle until the divider
      -- overflow raises gamma or the budget runs out
      if self.gamma ~= 0 or (self.read_k() & 0xf) ~= 0 then
        left = left - 1
        clock_div(self, 2)
        self.halt = false
        self.cb = 0
        do_branch(self, 0, 0, 0)   -- SM500 wakeup vector
      else
        local ticks = 0
        while ticks < 2 * left and self.gamma == 0 do
          clock_div(self, 1)
          ticks = ticks + 1
        end
        left = left - (ticks + 1) // 2
        if self.gamma == 0 then return n end
      end
    else
      self.prev_op = self.op
      self.prev_pc = self.pc
      self.op = self.prg[self.pc]
      increment_pc(self)
      local cycles = 1
      if self.op == 0x5e or self.op == 0x5f then    -- 2-byte opcodes
        cycles = 2
        self.param = self.prg[self.pc]
        increment_pc(self)
      end
      if self.skip then
        self.skip = false
        self.op = 0
      else
        execute_one(self)
      end
      left = left - cycles
      clock_div(self, 2 * cycles)
    end
  end
  return n
end

-- current LCD segment state, addressed as MAME's write_segs does:
-- key = o*2+h, value = 4-bit segment group (h0 = W latch, h1 = W')
function cpu:segments()
  local segs = {}
  for h = 0, 1 do
    for o = 0, O_PINS - 1 do
      local seg = (h == 1) and self.ox[o] or self.o[o]
      if (self.bp & 1) == 0 then seg = 0 end
      segs[o * 2 + h] = seg
    end
  end
  return segs
end

function M.load_rom(path)
  local f = assert(io.open(path, 'rb'))
  local data = f:read('a')
  f:close()
  return data
end

return M
