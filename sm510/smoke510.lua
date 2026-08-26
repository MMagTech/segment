package.path = './?.lua;' .. package.path
local sm510 = require 'sm510'
-- synthetic ROM exercising a spread of opcodes; not a real game, just a
-- crash/structure test. Fill 0x1000 with a mix, ensure no Lua errors and
-- that state advances.
local bytes = {}
local seq = {0x20,0x08,0x40,0x62,0x63,0x61,0x59,0x60,0x01,0x53,0x66,0x67,
             0x6b,0x64,0x6c,0x0a,0x0b,0x03,0x02,0x0c,0x04,0x18,0x10,0x14,
             0x1c,0x51,0x52,0x58,0x5a,0x5b,0x5e,0x68,0x69,0x6a,0x6d,0x6e}
for a=0,0xfff do bytes[a+1] = string.char(seq[(a % #seq)+1]) end
local rom = table.concat(bytes)
local sout, rout = 0, 0
local cpu = sm510.new{
  rom = rom,
  read_k = function() return 0 end,
  write_s = function(s) sout = s end,
  write_r = function(r) rout = r end,
}
cpu:reset()
assert(cpu.pc == (3<<10 | 7<<6), 'reset vector wrong: '..cpu.pc)
print(string.format('reset OK, PC=%04X', cpu.pc))
local ok, err = pcall(function() cpu:run(16384) end)   -- 1 second
if not ok then print('RUNTIME ERROR:', err); os.exit(1) end
print(string.format('ran 16384 cycles OK; PC=%04X ACC=%X DIV=%04X S=%02X',
  cpu.pc, cpu.acc, cpu.div, sout))
-- segments() must not error and returns a table
local segs = cpu:segments()
local n = 0; for _ in pairs(segs) do n = n + 1 end
print('segments() OK, '..n..' lit on synthetic state')
-- LCD RAM write path: poke lcd_ram_a[3]=0b0101 -> segments 0.3.0 and 0.3.2
cpu.ram[0x60+3] = 0x5
segs = cpu:segments()
print('after poking lcd_ram_a[3]=0x5:',
  segs['0.3.0'] and 'seg 0.3.0 ON' or 'MISS', segs['0.3.2'] and '0.3.2 ON' or 'MISS',
  segs['0.3.1'] and '0.3.1 (should be off!)' or '0.3.1 off')
