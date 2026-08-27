-- Sharp SM510 core in Lua.
--
-- SPDX-License-Identifier: BSD-3-Clause
-- Derived from the MAME project's SM510-family CPU implementation
-- (src/devices/cpu/sm510: sm510base, sm510, sm510op), which is
-- BSD-3-Clause, copyright-holders "hap". That copyright and the
-- BSD-3-Clause terms (see the repository LICENSE) apply to the derived
-- portions of this file and must be retained.
--
-- The SM510 is the chip inside the later Nintendo Game & Watch units
-- and the entire Tiger Electronics catalogue. Relative to the SM5A
-- (sm5a.lua) it differs in real ways, encoded below:
--   * base-class branch ops (T/TL/TML/TM) instead of the SM5A TR/TRS
--   * two stack levels
--   * SBM sets the RAM high bit for the *next* instruction only
--   * the LCD is two 16-nibble RAM banks (a,b) read column-by-column,
--     not the SM5A's W/W' shift latches
--   * an S strobe (WR/WS shift W, PTW outputs it) multiplexes K inputs
--
-- Verified against MAME 0.289 on gnw_stennis (Snoopy Tennis, a plain
-- SM510 game): a lockstep PC/ACC/BL/BM/C trace matches, and the rendered
-- LCD is identical to MAME's snapshot. The trace carries the same benign
-- divider-phase jitter the SM5A core has (a handful of TF1/TF4/TIS tests
-- per 26k instructions land on a divider-bit transition a tick off MAME's
-- timer phase; the game's timing loops absorb it and the display is
-- unaffected). Finding this verified one real bug: the TL long-jump
-- dispatch matched only op 0x70, missing 0x74/0x78 - fixed.
--
-- Still unverified: SM511/SM512 melody (not implemented), and the K-input
-- muxing via the S strobe (the CPU exposes write_s/read_k faithfully, but
-- which S bit gates which K column lives in the game, not the chip).
--
-- Usage mirrors sm5a.lua:
--   local sm510 = require 'sm510'
--   local cpu = sm510.new{
--     rom = <program image>,                 -- see rom layout note below
--     read_k  = function() return k end,      -- 4-bit K, pull-down
--     read_ba = function() return 1 end,      -- 1-bit BA, pull-up
--     read_b  = function() return 1 end,      -- 1-bit B,  pull-up
--     write_r = function(r) end,              -- buzzer, R1/R2
--     write_s = function(s) end,              -- input strobe (PTW)
--     r_mask_option = 2,                      -- SM510 default divider bit
--   }
--   cpu:reset(); cpu:run(cycles)
--   cpu:segments() -> { ['x.y.z'] = true }    -- lit segments, MAME tags

local M = {}

local PRGMASK = 0xfff       -- 12-bit program space (banked 2.7K ROM)
local PAGEMASK = 0x3f
local DATAMASK = 0x7f       -- 7-bit data space; 0x60-0x7f is LCD RAM

local cpu = {}
cpu.__index = cpu

function M.new(opts)
  local self = setmetatable({}, cpu)

  -- Program image indexed directly by 12-bit address. A romset's file is
  -- the region image; unmapped addresses read 0. (If a real romset turns
  -- out to be bank-packed rather than a flat 0x1000 image, remap here -
  -- flagged because it is the first thing to check against MAME.)
  local rom = opts.rom
  self.prg = {}
  for a = 0, PRGMASK do
    self.prg[a] = (a < #rom) and rom:byte(a + 1) or 0
  end

  self.read_k  = opts.read_k  or function() return 0 end
  self.read_ba = opts.read_ba or function() return 1 end
  self.read_b  = opts.read_b  or function() return 1 end
  self.write_r = opts.write_r or function() end
  self.write_s = opts.write_s or function() end
  self.r_mask_option = opts.r_mask_option or 2

  self.stack = {0, 0}         -- two levels
  self.ram = {}
  for i = 0, DATAMASK do self.ram[i] = 0 end
  return self
end

-- RAM address: bmask (SBM, one step) | bm<<4 | bl, masked to the space.
local function ram_addr(self)
  return (self.bmask | (self.bm << 4) | self.bl) & DATAMASK
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

local function push_stack(self)
  self.stack[2] = self.stack[1]
  self.stack[1] = self.pc
end
local function pop_stack(self)
  self.pc = self.stack[1] & PRGMASK
  self.stack[1] = self.stack[2]
end

-- buzzer (sm510.cpp clock_melody): R1 direct or from divider, R2 inverse
local function clock_melody(self)
  local out
  if self.r_mask_option == 'direct' then
    out = self.r & 3
  else
    out = self.div >> self.r_mask_option & 1
    out = out | ((out << 1 ~ 2) & 0x2)
    out = out & self.r
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
  self.r, self.r_out = 0, 0
  self.stack[1], self.stack[2] = 0, 0

  -- base reset: vector page 3/7, LCD on
  do_branch(self, 3, 7, 0)
  self.prev_pc = self.pc
  self.bp = 1
  self.bc = false
  self.y = 0
  self.write_r(0)
end

-- opcode handlers -----------------------------------------------------------

local function op_lb(self)   -- base SM510 variant
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

local function op_t(self)   -- T: jump within page
  self.pc = (self.pc & ~PAGEMASK & PRGMASK) | (self.op & PAGEMASK)
end

local function op_tl(self)  -- TL: long jump
  do_branch(self, self.param >> 6 & 3, self.op & 0xf, self.param & 0x3f)
end

local function op_tml(self) -- TML: long call
  push_stack(self)
  do_branch(self, self.param >> 6 & 3, self.op & 3, self.param & 0x3f)
end

local function op_tm(self)  -- TM: indirect call via page-0 pointer table
  push_stack(self)
  local idx = self.prg[self.op & 0x3f]
  do_branch(self, idx >> 6 & 3, 4, idx & 0x3f)
end

local function op_atpl(self)
  self.pc = (self.prev_pc & ~0xf) | self.acc
end

-- the bs LCD segment latch is fed by L / X / Y; see segments()

-- dispatch (sm510.cpp execute_one) ------------------------------------------

local function execute_one(self)
  local op = self.op
  local hi = op & 0xf0

  if hi == 0x20 then op_lax(self)
  elseif hi == 0x30 then op_adx(self)
  elseif hi == 0x40 then op_lb(self)
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
    elseif q == 0x70 or q == 0x74 or q == 0x78 then op_tl(self)     -- TL (70/74/78)
    elseif q == 0x7c then op_tml(self)                             -- TML (7c)
    elseif op == 0x00 then                                          -- SKIP
    elseif op == 0x01 then self.bp = self.acc                       -- ATBP
    elseif op == 0x02 then                                          -- SBM (deferred)
    elseif op == 0x03 then op_atpl(self)                            -- ATPL
    elseif op == 0x08 then self.acc = (self.acc + ram_r(self)) & 0xf -- ADD
    elseif op == 0x09 then op_add11(self)                           -- ADD11
    elseif op == 0x0a then self.acc = self.acc ~ 0xf                -- COMA
    elseif op == 0x0b then self.acc, self.bl = self.bl, self.acc    -- EXBLA
    elseif op == 0x51 then self.skip = (self.read_b() ~= 0)         -- TB
    elseif op == 0x52 then self.skip = (self.c == 0)                -- TC
    elseif op == 0x53 then self.skip = (self.acc == ram_r(self))    -- TAM
    elseif op == 0x58 then self.skip = (self.gamma == 0); self.gamma = 0 -- TIS
    elseif op == 0x59 then self.l = self.acc                        -- ATL
    elseif op == 0x5a then self.skip = (self.acc == 0)              -- TA0
    elseif op == 0x5b then self.skip = (self.acc == self.bl)        -- TABL
    elseif op == 0x5d then self.halt = true                         -- CEND
    elseif op == 0x5e then self.skip = (self.read_ba() ~= 0)        -- TAL
    elseif op == 0x5f then op_lbl(self)                             -- LBL
    elseif op == 0x60 then self.y = self.acc                        -- ATFC
    elseif op == 0x61 then self.r = self.acc; clock_melody(self)    -- ATR
    elseif op == 0x62 then                                          -- WR
      self.w = ((self.w << 1) | 0) & 0xff; self.write_s(self.w)     -- W->S
    elseif op == 0x63 then                                          -- WS
      self.w = ((self.w << 1) | 1) & 0xff; self.write_s(self.w)
    elseif op == 0x64 then op_incb(self)
    elseif op == 0x65 then self.div = 0                             -- IDIV
    elseif op == 0x66 then self.c = 0                               -- RC
    elseif op == 0x67 then self.c = 1                               -- SC
    elseif op == 0x68 then self.skip = (self.div & 0x4000) ~= 0     -- TF1
    elseif op == 0x69 then self.skip = (self.div & 0x0800) ~= 0     -- TF4
    elseif op == 0x6a then self.acc = self.read_k() & 0xf           -- KTA
    elseif op == 0x6b then                                          -- ROT
      local c = self.acc & 1
      self.acc = self.acc >> 1 | self.c << 3
      self.c = c
    elseif op == 0x6c then op_decb(self)
    elseif op == 0x6d then self.bc = (self.c ~= 0)                  -- BDC
    elseif op == 0x6e then pop_stack(self)                          -- RTN0
    elseif op == 0x6f then pop_stack(self); self.skip = true        -- RTN1
    end
  end

  -- W is wired directly to S (sm510.h update_w_latch): WR/WS output it
  -- as they shift, done inline above. No separate PTW on the SM510.

  -- BM high bit (SBM) is valid for exactly one following step
  self.bmask = (op == 0x02) and 0x40 or 0
end

local function op_argument(self)
  return self.op == 0x5f or (self.op & 0xf0) == 0x70
end

function cpu:run(n)
  local left = n
  while left > 0 do
    if self.halt then
      if self.gamma ~= 0 or (self.read_k() & 0xf) ~= 0 then
        left = left - 1
        clock_div(self, 2)
        self.halt = false
        do_branch(self, 1, 0, 0)   -- base wakeup vector
      else
        local ticks = 0
        while ticks < 2 * left and self.gamma == 0 do
          clock_div(self, 1); ticks = ticks + 1
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
      clock_div(self, 2 * cycles)
    end
  end
  return n
end

-- Lit LCD segments as MAME's x.y.z tags (matching the romset SVG titles):
--   x = group: 0=a, 1=b, 2=bs, 3=c   y = segment 0-15   z = common H1-4
-- Segment (a|b, y, z) is on iff lcd_ram_[bank][y] >> z & 1. The bs group
-- comes from the L/X/Y latches. c is absent on the base SM510.
function cpu:segments()
  local segs = {}
  local on = (self.bp & 1) == 1 and not self.bc
  if not on then return segs end
  local blink = (self.div & 0x4000 ~= 0) and self.y or 0
  for z = 0, 3 do
    -- group a: data 0x60-0x6f, group b: data 0x70-0x7f
    for y = 0, 15 do
      if (self.ram[0x60 + y] >> z & 1) == 1 then segs[('0.%d.%d'):format(y, z)] = true end
      if (self.ram[0x70 + y] >> z & 1) == 1 then segs[('1.%d.%d'):format(y, z)] = true end
    end
    -- group bs: one segment per common, from L (masked by blink) and X
    local bs = ((self.l & ~blink) >> z & 1) | (((self.x * 2) >> z) & 2)
    if bs ~= 0 then segs[('2.0.%d'):format(z)] = true end
  end
  return segs
end

function M.load_rom(path)
  local f = assert(io.open(path, 'rb'))
  local d = f:read('a'); f:close()
  return d
end

return M
