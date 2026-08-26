package.path = './?.lua;' .. package.path
local sm5a = require 'sm5a'
local edges = {}
local cyc = 0
local k = 0
local cpu
cpu = sm5a.new{
  rom = sm5a.load_rom('../roms/gnw_ball_extracted/ac-01'),
  read_k = function() return k end,
  write_r = function(out) edges[#edges+1] = { cpu and cpu.div or 0, cyc, out } end,
}
cpu:reset()
local function run(n) for i=1,n do cpu:run(1); cyc = cyc + 1 end end
run(3*16384)             -- splash
k = 4; run(4096); k = 0  -- press Game A
run(6*16384)             -- let the game run: balls step, maybe a miss
print('#edges', #edges)
-- Print edge timeline as deltas, bit0 = buzzer drive
local prev = 0
for i = 1, math.min(#edges, 80) do
  local e = edges[i]
  io.write(string.format('%8d +%-6d out=%X b=%d\n', e[2], e[2]-prev, e[3], e[3] & 1))
  prev = e[2]
end
