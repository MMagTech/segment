-- Sharp SM530 core in Lua.
--
-- SPDX-License-Identifier: BSD-3-Clause
-- Derived from the MAME project's SM510-family CPU implementation
-- (src/devices/cpu/sm510: sm510base, sm511, sm530), which is
-- BSD-3-Clause, copyright-holders "hap". That copyright and the
-- BSD-3-Clause terms (see the repository LICENSE) apply to the derived
-- portions of this file and must be retained.
--
-- The SM530 is the watch-family chip (Nelsonic game watches, Konami's
-- later units): 2K program, one stack level, an 8-bit K input read as
-- two nibbles (KTA low, KETA high), a four-flag gamma (10s/1s/0.5s/
-- 0.1s, tested per-flag by TG x), dedicated 1s and 1/100s counters,
-- a display-enable latch (SDS/RDS), an F output port, and the SM511's
-- melody controller with a 0xff step mask. The LCD is 12 groups of 4
-- segments across 2 commons, read straight out of two RAM banks; tags
-- follow the SM500 convention o.y.h, which is what the Nelsonic SVGs
-- title their segments.
--
-- Usage mirrors sm511.lua:
--   local sm530 = require 'sm530'
--   local cpu = sm530.new{
--     rom = <2K program image>, melody = <256-byte melody ROM>,
--     read_k = function() return k8 end,     -- 8-bit: K low, KE high
--     read_ba = ..., read_b = ..., write_r = ..., write_s = ...,
--   }

local M = {}

local PRGMASK = 0x7ff
local PAGEMASK = 0x3f
local DATAMASK = 0x7f

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

  self.read_k  = opts.read_k  or function() return 0 end
  self.read_ba = opts.read_ba or function() return 1 end
  self.read_b  = opts.read_b  or function() return 1 end
  self.write_r = opts.write_r or function() end
  self.write_s = opts.write_s or function() end
  self.write_f = opts.write_f or function() end

  self.stack = {0}
  self.ram = {}
  for i = 0, DATAMASK do self.ram[i] = 0 end
  return self
end

local function ram_addr(self)
  -- SABM sets the BM high bit, SABL the BL high bit, each for one step
  return ((self.bmask & 0x40) | ((self.bm << 4) & 0x30)
          | ((self.bmask & 0x08) | self.bl) ) & DATAMASK
end
local function ram_r(self) return self.ram[ram_addr(self)] end
local function ram_w(self, v) self.ram[ram_addr(self)] = v & 0xf end
local function bitmask(op) return 1 << (op & 3) end

local function increment_pc(self)
  local pc = self.pc
  local feed = (((pc >> 1 ~ pc) & 1) == 1) and 0 or 0x20
  self.pc = feed | (pc >> 1 & 0x1f) | (pc & ~PAGEMASK & PRGMASK)
end

local function do_branch(self, pu, pl)
  self.pc = ((pu << 6) | (pl & 0x3f)) & PRGMASK
end

local function push_stack(self)
  self.stack[1] = self.pc
end
local function pop_stack(self)
  self.pc = self.stack[1] & PRGMASK
end

local function clock_melody(self)
  if not self.has_melody then return end
  local cmd = self.melody[self.mel_addr & 0xff] & 0x3f
  local note = cmd & 0xf
  local out = 0
  if note >= 2 and note <= 13 then
    out = self.mel_duty_index & self.mel_rd & 1
    self.mel_duty_count = self.mel_duty_count + 1
    local index = (self.mel_duty_index << 4) | note
    local shift = (~cmd >> 4) & 1
    if self.mel_duty_count >= (TONE[index] << shift) then
      self.mel_duty_count = 0
      self.mel_duty_index = (self.mel_duty_index + 1) & 3
    end
  elseif note == 1 then
    self.mel_rd = self.mel_rd | 2
  end
  if (self.div & 0xff) == 0 then          -- 0xff step mask on the SM530
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

-- divider (sm530.cpp div_timer_cb): four gamma flags and two counters
local function clock_div(self, n)
  for _ = 1, n do
    self.div = (self.div + 1) & 0x7fff
    if self.div == 0 then
      self.gamma = self.gamma | 2                 -- 1s
      self.count_1s = (self.count_1s + 1) % 10
      if self.count_1s == 0 then
        self.gamma = self.gamma | 1               -- 10s
      end
    end
    if (self.div & 0x3fff) == 0 then
      self.gamma = self.gamma | 4                 -- 0.5s
    end
    if (self.div & 0xff) < 250 then
      self.subdiv = (self.subdiv + 1) % 32000
      if (self.subdiv % 320) == 0 then
        self.count_10ms = (self.count_10ms + 1) % 10
        if self.count_10ms == 0 then
          self.gamma = self.gamma | 8             -- 0.1s
        end
      end
    end
    clock_melody(self)
  end
end

function cpu:reset()
  self.pc, self.prev_pc = 0, 0
  self.op, self.prev_op, self.param = 0, 0, 0
  self.acc, self.bl, self.bm, self.c = 0, 0, 0, 0
  self.bmask = 0
  self.skip, self.halt = false, false
  self.div, self.gamma = 0, 0
  self.subdiv, self.count_1s, self.count_10ms = 0, 0, 0
  self.r_out = 0
  self.stack[1] = 0
  self.mel_rd, self.mel_step, self.mel_duty_count = 0, 0, 0
  self.mel_duty_index, self.mel_addr = 0, 0
  self.clk_div = 4
  self.ds = true                -- display on
  self.bp = 0

  do_branch(self, 0xf, 0)      -- SM530 reset vector
  self.prev_pc = self.pc
  self.write_r(0)
end

-- opcode handlers -----------------------------------------------------------

local function op_lb(self)                        -- SM530 partial LB
  local op = self.op
  self.bl = ((op << 2) & 8) | (op & 1) | 6
  self.bm = (op >> 2) & 3
end

local function op_lbl(self)
  self.bl = self.param & 0xf
  self.bm = (self.param & DATAMASK) >> 4
end

local function op_incb(self)
  self.bl = (self.bl + 1) & 0xf
  self.skip = ((self.bl & 7) == 0)                -- overflow on 3rd bit
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

local function op_adx(self)                       -- SM530: always skips on carry
  self.acc = self.acc + (self.op & 0xf)
  self.skip = (self.acc & 0x10) ~= 0
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
  do_branch(self, ((self.op << 2) | (self.param >> 6 & 3)), self.param & 0x3f)
end

local function op_trs(self)                       -- vectors on page 14
  push_stack(self)
  local jump = self.prg[((14 << 6) | (self.op & 0x3f)) & PRGMASK]
  local pu = ((jump >> 5 & 1) << 2) | ((jump >> 7 & 1) << 1) | (jump >> 6 & 1)
  do_branch(self, pu, jump & 0x1f)
end

local function op_atpl(self)
  self.pc = (self.prev_pc & ~0xf) | self.acc
end

-- dispatch (sm530.cpp execute_one) ------------------------------------------

local function execute_one(self)
  local op = self.op
  local hi = op & 0xf0

  if hi == 0x00 then op_adx(self)
  elseif hi == 0x10 then op_lax(self)
  elseif hi == 0x30 then op_lb(self)
  elseif hi >= 0x80 and hi <= 0xb0 then op_t(self)
  elseif hi >= 0xc0 then op_trs(self)
  else
    local q = op & 0xfc
    if     q == 0x20 then op_lda(self)
    elseif q == 0x24 then op_exc(self)
    elseif q == 0x28 then op_exc(self); op_incb(self)               -- EXCI
    elseif q == 0x2c then op_exc(self); op_decb(self)               -- EXCD
    elseif q == 0x40 then ram_w(self, ram_r(self) & ~bitmask(op))   -- RM
    elseif q == 0x44 then ram_w(self, ram_r(self) | bitmask(op))    -- SM
    elseif q == 0x48 then self.skip = (ram_r(self) & bitmask(op)) ~= 0  -- TM
    elseif q == 0x60 or q == 0x64 then op_tl(self)
    elseif q == 0x6c then                                           -- TG x
      self.skip = (self.gamma & bitmask(op)) ~= 0
      self.gamma = self.gamma & ~bitmask(op)
    elseif op == 0x4c then op_incb(self)
    elseif op == 0x4d then op_decb(self)
    elseif op == 0x4e then self.ds = false                          -- RDS
    elseif op == 0x4f then self.ds = true                           -- SDS
    elseif op == 0x50 then self.acc = self.read_k() & 0xf           -- KTA
    elseif op == 0x51 then self.acc = (self.read_k() >> 4) & 0xf    -- KETA
    elseif op == 0x52 then self.acc = self.count_10ms               -- DTA
    elseif op == 0x53 then self.acc = self.acc ~ 0xf                -- COMA
    elseif op == 0x54 then self.acc = (self.acc + ram_r(self)) & 0xf -- ADD
    elseif op == 0x55 then op_add11(self)                           -- ADDC
    elseif op == 0x56 then self.c = 0                               -- RC
    elseif op == 0x57 then self.c = 1                               -- SC
    elseif op == 0x58 then self.skip = (self.acc == self.bl)        -- TABL
    elseif op == 0x59 then self.skip = (self.acc == ram_r(self))    -- TAM
    elseif op == 0x5a then self.acc, self.bl = self.bl, self.acc    -- EXBL
    elseif op == 0x5b then self.skip = (self.c == 0)                -- TC
    elseif op == 0x5c then self.write_s(self.acc)                   -- ATS
    elseif op == 0x5d then self.write_f(self.acc)                   -- ATF
    elseif op == 0x5e then self.bp = self.acc                       -- ATBP
    elseif op == 0x68 then pop_stack(self)                          -- RTN
    elseif op == 0x69 then pop_stack(self); self.skip = true        -- RTNS
    elseif op == 0x6a then op_atpl(self)
    elseif op == 0x6b then op_lbl(self)
    elseif op == 0x70 then                                          -- IDIV
      self.div, self.subdiv, self.count_1s = 0, 0, 0
    elseif op == 0x71 then self.count_10ms = 0                      -- INIS
    elseif op == 0x72 then                                          -- SABM
    elseif op == 0x73 then                                          -- SABL
    elseif op == 0x74 then self.halt = true                         -- CEND
    elseif op == 0x75 then                                          -- TMEL
      self.skip = ((self.mel_rd & 2) ~= 0)
      self.mel_rd = self.mel_rd & ~2
    elseif op == 0x76 then self.mel_rd = self.mel_rd & ~1           -- RME
    elseif op == 0x77 then self.mel_rd = self.mel_rd | 1            -- SME
    elseif op == 0x78 then                                          -- PRE
      self.mel_addr = self.param
      self.mel_step = 0
    elseif op == 0x79 then self.skip = (self.read_ba() ~= 0)        -- TBA
    end
  end

  -- SABM/SABL are each valid for exactly one following step
  self.bmask = (self.op == 0x72) and 0x40 or ((self.op == 0x73) and 0x08 or 0)
end

local function op_argument(self)
  local op = self.op
  return op == 0x6b or op == 0x78 or (op & 0xf8) == 0x60
end

function cpu:run(n)
  local left = n
  while left > 0 do
    if self.halt then
      if self.gamma ~= 0 or (self.read_k() & 0xff) ~= 0 then
        left = left - 1
        clock_div(self, self.clk_div)
        self.halt = false
        do_branch(self, 0, 0)
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

-- Lit segments as o.y.h tags (SM500 convention): the LCD RAM banks at
-- 0x40-0x4b (H1) and 0x50-0x5b (H2), 12 groups of 4 segments, gated by
-- the display-enable latch.
function cpu:segments()
  local segs = {}
  if not self.ds then return segs end
  for o = 0, 11 do
    for y = 0, 3 do
      if (self.ram[0x40 + o] >> y & 1) == 1 then segs[('%d.%d.0'):format(o, y)] = true end
      if (self.ram[0x50 + o] >> y & 1) == 1 then segs[('%d.%d.1'):format(o, y)] = true end
    end
  end
  return segs
end

function cpu:melody_hz()
  if self.mel_rd & 1 == 0 then return nil end
  local cmd = self.melody[self.mel_addr & 0xff] & 0x3f
  local note = cmd & 0xf
  if note < 2 or note > 13 then return nil end
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
