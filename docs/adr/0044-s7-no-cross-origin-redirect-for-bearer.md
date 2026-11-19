# [S7] Authorization 不随跨 origin 跳转转发,守卫只装在探测路径

- 日期:2026-11-19
- 状态:已接受

## 背景

`urllib` 的默认重定向处理**保留请求头**,只剥 `Content-Length` 与
`Content-Type`。于是一个被攻破或恶意配置的模型端点,只要回 `302 Location:
http://attacker/`,本机就会把 `Authorization: Bearer <api_key>` 明文发给那个
地址。

`SECURITY.md:180` 把「密钥经 API / 日志 / 诊断 / 错误信息泄漏」列为
in-scope。这个问题是在实现探测端点时发现的(探测会带着密钥对用户填写的地址
发请求),但它对既有的 `complete()` 同样成立。

## 决策

新增 `SameHostRedirectHandler`(`kbapp/integrations/cloud.py:303`),在
`redirect_request` 里比较前后两次请求的 origin,**不同则返回 `None`**,让
urllib 拒绝跟随并抛 `HTTPError`,由调用方归入 `redirect_refused`。

origin 比较前做规范化:scheme 与 host 转小写,并补上 scheme 的默认端口
(`http`→80,`https`→443)。理由是同源但写成 `https://h:443` 或 host 大小写
变化时并不跨域,把它报成「拒绝了一个跳转」会给出与实际原因不符的结论;
端口不同仍然拒绝。

守卫只通过 `open_cloud(..., guard_redirects=True)` 装在**探测**路径上。
既有 `complete()` 的传输行为一个字没改——那条路径已经在产出真实费用记录,
改动需要单独的评审与测试。

## 备选

- **全局默认开启**。这是最终形态,但它会改变一条已上线的写路径(且现有
  用例 patch 的是 `cloud.urlopen`,守卫路径走 opener,测试要跟着改)。已立
  #80 单独处理,不在探测 PR 里顺手改。
- **完全不跟随任何跳转**。否决:同源跳转(例如 `/v1` → `/v1/`)是合法的,
  一律拒绝会把正常端点判成故障。
- **只比较 host,不比较 scheme**。否决:`https` → `http` 是同主机但明文传输,
  正是要拦的情况。
- **比较原始 `netloc`**。否决:`https://h` 与 `https://h:443` 是同一 origin,
  逐字符比较会误报。

## 后果

探测不会把密钥转发出去;`redirect_refused` 有专门的文案,不混进通用
「端点返回了异常状态」。

代价是 `complete()` 仍带着这个暴露面——已立 #80 记录,状态是「已知且有意
延后」,不是「已修复」。301 也归入 `redirect_refused`:它是最常见的永久跳转
(域名迁移、http→https),守卫同样拦下了它,若归到通用 http,界面会显示
「端点返回了异常状态(HTTP 301)」而不是「我们拒绝了一个跳转」。
