package.path = './?.lua;' .. package.path
local sm510 = require 'sm510'
local cpu = sm510.new{ rom = sm510.load_rom(arg[1]) }
cpu:reset()
local out = io.open(arg[2], 'w')
local budget = tonumber(arg[3]) or (2 * 16384)
while budget > 0 do
  if cpu.halt then cpu:run(1); budget = budget - 1; if cpu.halt then break end
  else
    if not cpu.skip then
      out:write(string.format('%04X A=%X BL=%X BM=%X C=%X\n',
        cpu.pc, cpu.acc, cpu.bl, cpu.bm, cpu.c))
    end
    cpu:run(1); budget = budget - 1
  end
end
out:close()
