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
for f = 1, 900 do
  if f >= 300 and f < 320 then k = 4 else k = 0 end
  edges = 0
  acc = acc + 16384/60
  local c = acc // 1
  acc = acc - c
  cpu:run(c)
  if edges > 0 then io.write(string.format('frame %d: %d edges\n', f, edges)) end
end
