package.path = './?.lua;' .. package.path
local sm5a = require 'sm5a'
local k = 0
local cpu = sm5a.new{
  rom = sm5a.load_rom('../roms/gnw_ball_extracted/ac-01'),
  read_k = function() return k end,
}
cpu:reset()
cpu:run(5 * 16384)          -- attract mode
k = 0x04                    -- hold GAME A
cpu:run(16384 // 4)         -- quarter second
k = 0
cpu:run(2 * 16384)          -- let the game settle
local segs = cpu:segments()
for o = 0, 8 do for h = 0, 1 do
  local v = segs[o*2+h]
  for y = 0, 3 do
    if (v >> y) & 1 == 1 then io.write(string.format('%d.%d.%d\n', o, y, h)) end
  end
end end
