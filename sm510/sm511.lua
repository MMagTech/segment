-- Sharp SM511/SM512 core in Lua.
--
-- SPDX-License-Identifier: BSD-3-Clause
-- Derived from the MAME project's SM510-family CPU implementation
-- (src/devices/cpu/sm510: sm510base, sm511, sm510op), which is
-- BSD-3-Clause, copyright-holders "hap". That copyright and the
-- BSD-3-Clause terms (see the repository LICENSE) apply to the derived
-- portions of this file and must be retained.
--
-- The SM511 is the melody sibling of the SM510 (sm510.lua): the chip in
-- the multi-screen Nintendo units and the musical Tiger games. It is
-- NOT an opcode superset; the map is rearranged, and that rearrangement
-- is the heart of this file:
--   * KTA moved to 0x50, ROT to 0x00; TF1/TF4 and ATR are gone
--   * TL owns the whole 0x70 row; TML sits at 0x68-0x6b
--   * a 0x60-prefixed two-byte page holds RME/SME/TMEL/ATFC/BDC/ATBP
--     and the CLKHI/CLKLO instruction-clock switch
--   * W shifts silently (WR/WS); PTW outputs the latch, PRE presets the
--     melody pointer
--   * the R pin belongs to the melody controller: a 256-byte melody ROM
--     of note commands, stepped on divider F7, synthesized through the
--     datasheet's tone-cycle table
--   * the instruction clock defaults to 8kHz (clk_div 4), switchable
--
-- The SM512 differs only in LCD RAM: a third bank (group c, tags 3.y.z)
-- at data 0x50-0x5f. Pass chip='sm512' to enable it.
--
-- Usage mirrors sm510.lua, plus the melody image:
--   local sm511 = require 'sm511'
--   local cpu = sm511.new{
--     rom = <4K program image>,
--     melody = <256-byte melody ROM>,        -- the romset's second file
--     chip = 'sm511' | 'sm512',
--     read_k/read_ba/read_b/write_r/write_s as before,
--   }
--   write_r receives the melody square wave (0/1) as it toggles.

local M = {}

local PRGMASK = 0xfff
local PAGEMASK = 0x3f
local DATAMASK = 0x7f

-- tone cycle table (SM511/SM512 datasheet fig.5); indexed duty<<4|note
local TONE = {
  [0]=0, 0, 7, 8, 8, 9, 9, 10,11,11,12,13,14,14, 0, 0,
      0, 0, 8, 8, 9, 9, 10,11,11,12,13,13,14,15, 0, 0,
      0, 0, 8, 8, 9, 9, 10,10,11,12,12,13,14,15, 0, 0,
      0, 0, 8, 9, 9, 10,10,11,11,12,13,14,14,15, 0, 0,
}

local cpu = {}
cpu.__index = cpu

function M.new(opts)
  local self = setmetatable({}, cpu)
  local rom = opts.rom
  self.prg = {}
  for a = 0, PRGMASK do
    self.prg[a] = (a < #rom) and rom:byte(a + 1) or 0
  end
  local mel = opts.melody or ''
  self.melody = {}
  for a = 0, 0xff do
    self.melody[a] = (a < #mel) and mel:byte(a + 1) or 0
  end
  self.has_melody = #mel > 0
  self.sm512 = (opts.chip == 'sm512')

  self.read_k  = opts.read_k  or function() return 0 end
  self.read_ba = opts.read_ba or function() return 1 end
  self.read_b  = opts.read_b  or function() return 1 end
  self.write_r = opts.write_r or function() end
  self.write_s = opts.write_s or function() end

  self.stack = {0, 0}
  self.ram = {}
  for i = 0, DATAMASK do self.ram[i] = 0 end
  return self
end

local function ram_addr(self)
  return (self.bmask | (self.bm << 4) | self.bl) & DATAMASK
end
local function ram_r(self) return self.ram[ram_addr(self)] end
local function ram_w(self, v) self.ram[ram_addr(self)] = v & 0xf end
local function bitmask(op) return 1 << (op & 3) end

local function increment_pc(self)
  local pc = self.pc
  local feed = (((pc >> 1 ~ pc) & 1) == 1) and 0 or 0x20
  self.pc = feed | (pc >> 1 & 0x1f) | (pc & ~PAGEMASK & PRGMASK)
end

local function do_branch(self, pu, pm, pl)
  self.pc = ((pu << 10) | (pm << 6 & 0x3c0) | (pl & 0x3f)) & PRGMASK
end

local function push_stack(self)
  self.stack[2] = self.stack[1]
  self.stack[1] = self.pc
end
local function pop_stack(self)
  self.pc = self.stack[1] & PRGMASK
  self.stack[1] = self.stack[2]
end

-- melody controller (sm511.cpp clock_melody), one call per divider tick
local function clock_melody(self)
  if not self.has_melody then return end
  local cmd = self.melody[self.mel_addr & 0xff] & 0x3f
  local note = cmd & 0xf
  local out = 0

  if note >= 2 and note <= 13 then
    out = self.mel_duty_index & self.mel_rd & 1
    self.mel_duty_count = self.mel_duty_count + 1
    local index = (self.mel_duty_index << 4) | note
    local shift = (~cmd >> 4) & 1          -- OCT
    if self.mel_duty_count >= (TONE[index] << shift) then
      self.mel_duty_count = 0
      self.mel_duty_index = (self.mel_duty_index + 1) & 3
    end
  elseif note == 1 then
    self.mel_rd = self.mel_rd | 2          -- stop flag
  end

  -- step the melody pointer on divider F7
  if (self.div & 0x7f) == 0 then
    local mask = ((cmd & 0x20) ~= 0) and 0x1f or 0x0f
    self.mel_step = (self.mel_step + 1) & mask
    if self.mel_step == 0 then
      self.mel_addr = (self.mel_addr + 1) & 0xff
    end
  end

  if out ~= self.r_out then
    self.r_out = out
    self.write_r(out)
  end
end

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
  self.bmask = 0
  self.skip, self.halt = false, false
  self.w = 0
  self.div, self.gamma = 0, 0
  self.l, self.x, self.y = 0, 0, 0
  self.bc = false
  self.r_out = 0
  self.stack[1], self.stack[2] = 0, 0
  self.mel_rd, self.mel_step, self.mel_duty_count = 0, 0, 0
  self.mel_duty_index, self.mel_addr = 0, 0
  self.clk_div = 4               -- SM511 boots on the 8kHz clock

  do_branch(self, 3, 7, 0)
  self.prev_pc = self.pc
  self.bp = 1
  self.bc = false
  self.y = 0
  self.write_r(0)
end

-- opcode handlers shared with sm510.lua (same semantics) --------------------

local function op_lb(self)
  local op = self.op
  self.bm = (self.bm & 4) | (op & 3)
  self.bl = (op >> 2 & 3) | (((op & 0xc) ~= 0) and 0xc or 0)
end

local function op_lbl(self)
  self.bl = self.param & 0xf
  self.bm = (self.param & DATAMASK) >> 4
end

local function op_incb(self)
  self.bl = (self.bl + 1) & 0xf
  self.skip = (self.bl == 0)
end

local function op_decb(self)
  self.bl = (self.bl - 1) & 0xf
  self.skip = (self.bl == 0xf)
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

local function op_t(self)
  self.pc = (self.pc & ~PAGEMASK & PRGMASK) | (self.op & PAGEMASK)
end

local function op_tl(self)
  do_branch(self, self.param >> 6 & 3, self.op & 0xf, self.param & 0x3f)
end

local function op_tml(self)
  push_stack(self)
  do_branch(self, self.param >> 6 & 3, self.op & 3, self.param & 0x3f)
end

local function op_tm(self)
  push_stack(self)
  local idx = self.prg[self.op & 0x3f]
  do_branch(self, idx >> 6 & 3, 4, idx & 0x3f)
end

local function op_atpl(self)
  self.pc = (self.prev_pc & ~0xf) | self.acc
end

-- dispatch (sm511.cpp execute_one) ------------------------------------------

local function execute_one(self)
  local op = self.op
  local hi = op & 0xf0

  if hi == 0x20 then op_lax(self)
  elseif hi == 0x30 then op_adx(self)
  elseif hi == 0x40 then op_lb(self)
  elseif hi == 0x70 then op_tl(self)                                -- whole row
  elseif hi >= 0x80 and hi <= 0xb0 then op_t(self)
  elseif hi >= 0xc0 then op_tm(self)
  else
    local q = op & 0xfc
    if     q == 0x04 then ram_w(self, ram_r(self) & ~bitmask(op))   -- RM
    elseif q == 0x0c then ram_w(self, ram_r(self) | bitmask(op))    -- SM
    elseif q == 0x10 then op_exc(self)
    elseif q == 0x14 then op_exc(self); op_incb(self)               -- EXCI
    elseif q == 0x18 then op_lda(self)
    elseif q == 0x1c then op_exc(self); op_decb(self)               -- EXCD
    elseif q == 0x54 then self.skip = (ram_r(self) & bitmask(op)) ~= 0  -- TMI
    elseif q == 0x68 then op_tml(self)                              -- TML 68-6b
    elseif op == 0x00 then                                          -- ROT
      local c = self.acc & 1
      self.acc = self.acc >> 1 | self.c << 3
      self.c = c
    elseif op == 0x01 then self.acc = self.div >> 11 & 0xf          -- DTA
    elseif op == 0x02 then                                          -- SBM (deferred)
    elseif op == 0x03 then op_atpl(self)                            -- ATPL
    elseif op == 0x08 then self.acc = (self.acc + ram_r(self)) & 0xf -- ADD
    elseif op == 0x09 then op_add11(self)                           -- ADD11
    elseif op == 0x0a then self.acc = self.acc ~ 0xf                -- COMA
    elseif op == 0x0b then self.acc, self.bl = self.bl, self.acc    -- EXBLA
    elseif op == 0x50 then self.acc = self.read_k() & 0xf           -- KTA
    elseif op == 0x51 then self.skip = (self.read_b() ~= 0)         -- TB
    elseif op == 0x52 then self.skip = (self.c == 0)                -- TC
    elseif op == 0x53 then self.skip = (self.acc == ram_r(self))    -- TAM
    elseif op == 0x58 then self.skip = (self.gamma == 0); self.gamma = 0 -- TIS
    elseif op == 0x59 then self.l = self.acc                        -- ATL
    elseif op == 0x5a then self.skip = (self.acc == 0)              -- TA0
    elseif op == 0x5b then self.skip = (self.acc == self.bl)        -- TABL
    elseif op == 0x5c then self.x = self.acc                        -- ATX
    elseif op == 0x5d then self.halt = true                         -- CEND
    elseif op == 0x5e then self.skip = (self.read_ba() ~= 0)        -- TAL
    elseif op == 0x5f then op_lbl(self)                             -- LBL
    elseif op == 0x61 then                                          -- PRE
      self.mel_addr = self.param
      self.mel_step = 0
    elseif op == 0x62 then self.w = ((self.w << 1) | 0) & 0xff      -- WR (silent)
    elseif op == 0x63 then self.w = ((self.w << 1) | 1) & 0xff      -- WS (silent)
    elseif op == 0x64 then op_incb(self)
    elseif op == 0x65 then self.div = 0                             -- IDIV
    elseif op == 0x66 then self.c = 0                               -- RC
    elseif op == 0x67 then self.c = 1                               -- SC
    elseif op == 0x6c then op_decb(self)
    elseif op == 0x6d then self.write_s(self.w)                     -- PTW
    elseif op == 0x6e then pop_stack(self)                          -- RTN0
    elseif op == 0x6f then pop_stack(self); self.skip = true        -- RTN1
    elseif op == 0x60 then                                          -- 2-byte page
      local p = self.param
      self.op = (0x60 << 8) | p       -- for prev_op/LAX purposes
      if     p == 0x30 then self.mel_rd = self.mel_rd & ~1          -- RME
      elseif p == 0x31 then self.mel_rd = self.mel_rd | 1           -- SME
      elseif p == 0x32 then                                         -- TMEL
        self.skip = ((self.mel_rd & 2) ~= 0)
        self.mel_rd = self.mel_rd & ~2
      elseif p == 0x33 then self.y = self.acc                       -- ATFC
      elseif p == 0x34 then self.bc = (self.c ~= 0)                 -- BDC
      elseif p == 0x35 then self.bp = self.acc                      -- ATBP
      elseif p == 0x36 then self.clk_div = 2                        -- CLKHI
      elseif p == 0x37 then self.clk_div = 4                        -- CLKLO
      end
    end
  end

  self.bmask = (self.op == 0x02) and 0x40 or 0
end

local function op_argument(self)
  local op = self.op
  return (op >= 0x5f and op <= 0x61) or (op & 0xf0) == 0x70
         or (op & 0xfc) == 0x68
end

function cpu:run(n)
  local left = n
  while left > 0 do
    if self.halt then
      if self.gamma ~= 0 or (self.read_k() & 0xf) ~= 0 then
        left = left - 1
        clock_div(self, self.clk_div)
        self.halt = false
        do_branch(self, 1, 0, 0)
      else
        local ticks = 0
        while ticks < self.clk_div * left and self.gamma == 0 do
          clock_div(self, 1); ticks = ticks + 1
        end
        left = left - (ticks + self.clk_div - 1) // self.clk_div
        if self.gamma == 0 then return n end
      end
    else
      self.prev_op = self.op
      self.prev_pc = self.pc
      self.op = self.prg[self.pc]
      increment_pc(self)
      local cycles = 1
      if op_argument(self) then
        cycles = 2
        self.param = self.prg[self.pc]
        increment_pc(self)
      end
      if self.skip then
        self.skip = false
        self.op = 0
        self.bmask = 0
      else
        execute_one(self)
      end
      left = left - cycles
      clock_div(self, self.clk_div * cycles)
    end
  end
  return n
end

-- Lit segments as x.y.z tags: 0=a (0x60), 1=b (0x70), 2=bs, 3=c (0x50,
-- SM512 only).
function cpu:segments()
  local segs = {}
  local on = (self.bp & 1) == 1 and not self.bc
  if not on then return segs end
  local blink = (self.div & 0x4000 ~= 0) and self.y or 0
  for z = 0, 3 do
    for y = 0, 15 do
      if (self.ram[0x60 + y] >> z & 1) == 1 then segs[('0.%d.%d'):format(y, z)] = true end
      if (self.ram[0x70 + y] >> z & 1) == 1 then segs[('1.%d.%d'):format(y, z)] = true end
      if self.sm512 and (self.ram[0x50 + y] >> z & 1) == 1 then
        segs[('3.%d.%d'):format(y, z)] = true
      end
    end
    local bs = ((self.l & ~blink) >> z & 1) | (((self.x * 2) >> z) & 2)
    if bs ~= 0 then segs[('2.0.%d'):format(z)] = true end
  end
  return segs
end

-- The melody note under the pointer, so the game shell can pick a tone
-- sample without re-deriving chip state: returns nil when silent, else
-- the R square wave's frequency in Hz.
function cpu:melody_hz()
  if self.mel_rd & 1 == 0 then return nil end
  local cmd = self.melody[self.mel_addr & 0xff] & 0x3f
  local note = cmd & 0xf
  if note < 2 or note > 13 then return nil end
  -- one duty step per TONE[index] divider ticks (32768Hz), full wave =
  -- 4 duty steps; average the four per-duty cycle counts
  local shift = (~cmd >> 4) & 1
  local total = 0
  for d = 0, 3 do total = total + (TONE[(d << 4) | note] << shift) end
  return 32768 / total
end

function M.load_rom(path)
  local f = assert(io.open(path, 'rb'))
  local d = f:read('a'); f:close()
  return d
end

return M
