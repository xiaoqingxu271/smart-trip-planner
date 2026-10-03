import { beforeEach, describe, expect, it } from 'vitest'
import { getAccessCode, imgProxy, setAccessCode } from './api'

beforeEach(() => {
  localStorage.clear()
})

describe('访问码存取', () => {
  it('set 后能读回', () => {
    setAccessCode('pw123')
    expect(getAccessCode()).toBe('pw123')
  })

  it('未设置时返回空串', () => {
    expect(getAccessCode()).toBe('')
  })
})

describe('imgProxy 图片代理 URL', () => {
  it('空地址返回 null', () => {
    expect(imgProxy(null)).toBeNull()
    expect(imgProxy(undefined)).toBeNull()
    expect(imgProxy('')).toBeNull()
  })

  it('无访问码时不带 code 参数', () => {
    const url = imgProxy('https://store.is.autonavi.com/a.jpg')
    expect(url).toBe('/api/utils/image?u=' + encodeURIComponent('https://store.is.autonavi.com/a.jpg'))
    expect(url).not.toContain('code=')
  })

  it('有访问码时附带 code 查询参数', () => {
    setAccessCode('pw&1=2')
    const url = imgProxy('https://store.is.autonavi.com/a.jpg')!
    expect(url).toContain('&code=' + encodeURIComponent('pw&1=2'))
    expect(url.startsWith('/api/utils/image?u=')).toBe(true)
  })
})
