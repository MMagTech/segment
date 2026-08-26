package.path = './?.lua;' .. package.path
local sm5a = require 'sm5a'
local edges, k = 0, 0
local cpu = sm5a.new{
  rom = sm5a.load_rom('../roms/gnw_ball_extracted/ac-01'),
  read_k = function() return k end,
  write_r = function(out) edges = edges + 1 end,
}
cpu:reset()
local acc = 0
local function frames(n, kk)
  k = kk
  for i = 1, n do
    acc = acc + 16384/60
    local c = acc // 1
    acc = acc - c
    cpu:run(c)
  end
end
frames(300, 0)
print('edges during splash:', edges)
frames(20, 4)     -- hold Game A 20 frames
print('edges after press :', edges)
frames(200, 0)
print('edges after 200 more:', edges)
