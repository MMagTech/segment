package.path = './?.lua;' .. package.path
local sm510 = require 'sm510'
local cpu = sm510.new{ rom = sm510.load_rom(arg[1]) }
cpu:reset()
cpu:run((tonumber(arg[2]) or 5) * 16384)
local segs = cpu:segments()
local keys = {}
for k in pairs(segs) do keys[#keys+1] = k end
table.sort(keys)
for _,k in ipairs(keys) do print(k) end
