import { beforeEach, describe, expect, it } from 'vitest'
import { clearToken, getAccessCode, getToken, imgProxy, setAccessCode, setToken } from './api'

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

describe('会话 Token 存取', () => {
  it('set 后能读回，clear 后为空', () => {
    setToken('tok_abc')
    expect(getToken()).toBe('tok_abc')
    clearToken()
    expect(getToken()).toBe('')
  })

  it('未设置时返回空串', () => {
    expect(getToken()).toBe('')
  })
})

describe('imgProxy 图片代理 URL', () => {
  it('空地址返回 null', () => {
    expect(imgProxy(null)).toBeNull()
    expect(imgProxy(undefined)).toBeNull()
    expect(imgProxy('')).toBeNull()
  })

  it('无凭证时不带 code/token 参数', () => {
    const url = imgProxy('https://store.is.autonavi.com/a.jpg')
    expect(url).toBe('/api/utils/image?u=' + encodeURIComponent('https://store.is.autonavi.com/a.jpg'))
  })

  it('有访问码时附带 code 查询参数', () => {
    setAccessCode('pw&1=2')
    const url = imgProxy('https://store.is.autonavi.com/a.jpg')!
    expect(url).toContain('code=' + encodeURIComponent('pw&1=2'))
    expect(url.startsWith('/api/utils/image?u=')).toBe(true)
  })

  it('有 Token 时不携带 token 参数（避免泄露到 URL/日志）', () => {
    setToken('tok_xyz')
    const url = imgProxy('https://store.is.autonavi.com/a.jpg')!
    expect(url).not.toContain('token')
    expect(url.startsWith('/api/utils/image?u=')).toBe(true)
  })

  it('两种凭证时仅携带 code，token 不拼进 URL', () => {
    setAccessCode('pw')
    setToken('tok')
    const url = imgProxy('https://a.com/b.png')!
    expect(url).toContain('code=pw')
    expect(url).not.toContain('token')
  })
})
