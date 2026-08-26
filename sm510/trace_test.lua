package.path = './?.lua;' .. package.path
local sm5a = require 'sm5a'
local cpu = sm5a.new{ rom = sm5a.load_rom('../roms/gnw_ball_extracted/ac-01') }
cpu:reset()
print(string.format('reset PC=%04X (expect 03C0)', cpu.pc))
-- trace first 40 instructions: PC and opcode as fetched
for i = 1, 40 do
  local pc, op = cpu.pc, cpu.prg[cpu.pc]
  cpu:run(1)
  io.write(string.format('%04X:%02X A=%X BL=%X BM=%X C=%X%s\n',
    pc, op, cpu.acc, cpu.bl, cpu.bm, cpu.c, cpu.halt and ' HALT' or ''))
  if cpu.halt then break end
end
