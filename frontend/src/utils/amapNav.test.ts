import { beforeEach, describe, expect, it, vi } from 'vitest'
import { buildNavUrl, buildSearchUrl, isMobileUA, openExternal } from './amapNav'

function setUA(ua: string) {
  Object.defineProperty(window.navigator, 'userAgent', { value: ua, configurable: true })
}

beforeEach(() => {
  setUA('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0')
})

describe('isMobileUA', () => {
  it('PC UA 返回 false', () => {
    expect(isMobileUA()).toBe(false)
  })

  it('iOS / Android UA 返回 true', () => {
    setUA('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1')
    expect(isMobileUA()).toBe(true)
    setUA('Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/126.0 Mobile')
    expect(isMobileUA()).toBe(true)
  })
})

describe('buildNavUrl 高德 URI API 导航链接', () => {
  const loc = { longitude: 116.397428, latitude: 39.90923 }

  it('PC 端 callnative=0（固定网页版）', () => {
    const url = buildNavUrl('故宫博物院', loc)
    expect(url).toContain('https://uri.amap.com/navigation?')
    expect(url).toContain('to=116.397428,39.90923,')
    expect(url).toContain('mode=car')
    expect(url).toContain('coordinate=gaode')
    expect(url).toContain('callnative=0')
  })

  it('移动端 callnative=1（尝试唤起 APP）', () => {
    setUA('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1')
    expect(buildNavUrl('故宫博物院', loc)).toContain('callnative=1')
  })

  it('名称做 URL 编码（中文名 / 括号后缀）', () => {
    const url = buildNavUrl('全聚德（前门店）', loc)
    expect(url).toContain(encodeURIComponent('全聚德（前门店）'))
    expect(url).not.toContain('全聚德')
  })

  it('带 src 来源标识', () => {
    expect(buildNavUrl('故宫博物院', loc)).toContain('src=smart-trip-planner')
  })
})

describe('buildSearchUrl 高德搜索页兜底', () => {
  it('带城市参数', () => {
    const url = buildSearchUrl('全聚德', '北京')
    expect(url).toContain('https://www.amap.com/search?')
    expect(url).toContain('query=' + encodeURIComponent('全聚德'))
    expect(url).toContain('city=' + encodeURIComponent('北京'))
  })

  it('无城市时省略 city 参数', () => {
    expect(buildSearchUrl('全聚德', '')).not.toContain('city=')
  })
})

describe('openExternal 打开方式', () => {
  it('PC 新标签页打开', () => {
    const spy = vi.spyOn(window, 'open').mockReturnValue(null)
    openExternal('https://uri.amap.com/navigation?to=1')
    expect(spy).toHaveBeenCalledWith('https://uri.amap.com/navigation?to=1', '_blank')
    spy.mockRestore()
  })
})
