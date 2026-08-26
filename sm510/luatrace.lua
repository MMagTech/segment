-- Emit execution trace in MAME tracelog format for diffing.
package.path = './?.lua;' .. package.path
local sm5a = require 'sm5a'
local cpu = sm5a.new{ rom = sm5a.load_rom('../roms/gnw_ball_extracted/ac-01') }
cpu:reset()
local out = io.open(arg[1] or 'lua_states.txt', 'w')
local budget = tonumber(arg[2]) or (3 * 16384)   -- 3 seconds
while budget > 0 do
  if cpu.halt then
    local before = budget
    cpu:run(1)                      -- burns idle time until wake or out of budget
    budget = budget - 1
    if cpu.halt then break end      -- budget exhausted while asleep
  else
    if not cpu.skip then            -- MAME's hook skips skipped ops
      out:write(string.format('%04X A=%X BL=%X BM=%X C=%X\n',
        cpu.pc, cpu.acc, cpu.bl, cpu.bm, cpu.c))
    end
    cpu:run(1)
    budget = budget - 1
  end
end
out:close()
