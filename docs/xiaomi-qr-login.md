# 小米相关集成的扫码登录实现对照

复核日期：2026-10-29。下列结论来自固定提交的公开源码，未使用用户账号或密钥。源码对照本身不能证明此前 HA 页面中“数据点稀疏”的根因。

## 结论

最直接可借鉴的 `Xiaomi-cloud-tokens-extractor` 和 `homebridge-miot` 都让小米服务器生成二维码：请求 `_qrsize=480`、`_hasLogo=false`，把响应的 `qr` 当图片地址，把 `loginUrl` 当浏览器登录地址。它们没有把 `qr` 字符串再交给本地二维码库。[A、B、C]

小米官方 `ha_xiaomi_home` 使用 OAuth 授权链接，在 HA 进度页展示可点击入口；它不是集成内部生成、展示二维码的参考实现。[D]

## 源码对照

| 项目 | 二维码来源与参数 | 展示方式与链接 |
| --- | --- | --- |
| Xiaomi-cloud-tokens-extractor | `GET /longPolling/loginUrl`，`_qrsize=480`、`_hasLogo=false`；`qr` 存入 `_qr_image_url`，`loginUrl` 单独保存 | 下载 `qr` 的原始响应字节，交给本地 HTTP 图片服务；失败时原样保存临时文件并用 Pillow 打开。另外输出 `loginUrl`。[A] |
| homebridge-miot | 同一接口，`_qrsize=480`、`_hasLogo=false`；返回 `qr`、`lp`、`loginUrl` | 前端 `img.src = data.qr`，CSS 为 `max-width:260px; width:100%; background:white; padding:8px`；链接指向 `data.loginUrl || data.qr`。[B、C] |
| dentra/esphome-components | 同一接口，`_qrsize=240`；`image_url=res['qr']`，`login_url=res['loginUrl']` | 获取原图字节，再将图片栅格采样成终端半块字符。先把 RGBA 合成到白底，避免透明像素被当黑色。Dashboard 因行距和主题色会破坏文本二维码，禁用这种渲染。[E、F] |
| 小米官方 ha_xiaomi_home | OAuth `gen_auth_url`，本次检查的 config flow 不生成 QR | `async_show_progress` 的说明占位符放入 `<a href=... target="_blank">`，等待 OAuth 回调。[D] |
| XiaomiGateway3 | 本次检查的云客户端以账号密码、验证码、验证票据及已有 token 登录 | 云客户端未包含上述 `longPolling/loginUrl` 二维码流程；不能用其验证码图片流程证明扫码登录如何生成。[G] |
| hass-xiaomi-miot | 本次检查的云客户端基于 `micloud.MiCloud`，维护 username/password、captcha 和验证流程 | 本次检查的云客户端没有 QR 生成或上述登录接口，不能视作现成 QR 渲染方案。[H] |

### 字段和图片格式的边界

`qr` 是图片资源 URL，`loginUrl` 是可打开的登录 URL，`lp` 是等待扫码结果的长轮询 URL；它们用途不同。ESPHome 客户端还明确将 `login_url` 注释为二维码编码的 URL。[A、B、E]

`_qrsize=480` 是请求参数，不足以单独证明响应一定是 480×480 或某个 MIME 类型。tokens-extractor 的临时文件名虽然使用 `.jpg`，但内容直接写入响应字节，没有 JPEG 编码步骤；其本地 HTTP 服务也未显式设置 Content-Type。因此不能据文件扩展名推断源图片格式。[A]

这些参考证明“远端原图 + 独立登录链接”是已有做法，但不能证明远端图片一定使用实心方块，也不能从第三方 CSS 断定 HA 页面存在缩放问题。[A、B、C]

## 对本插件的建议与待验证项

保留已可用的登录链接。要恢复二维码，先比较服务器原图和 HA 渲染结果，再决定是否有必要本地重生成。若本地生成，应编码登录载荷 `loginUrl`，不能编码 `qr` 图片下载地址；这是由上述字段用途推导出的实现建议，并非上述项目都已采用的做法。[A、B、E]

“每个数据点特别稀疏”仍可能涉及源图片样式、透明背景、缩放或渲染方式，需实际图片证据区分。检查透明 PNG 时，应先合成白底再统计黑白像素；ESPHome 的实现提供了明确参考。[E]

## 本次端点实测

通过插件现有 `start_qr_login` 创建未授权的新会话，只下载原图，没有执行登录或授权轮询。图片仅在内存处理，没有保存会话 URL、票据或二维码载荷。

- 返回 `image/png;charset=UTF-8`，尺寸 480×480，RGBA 的 alpha 范围为 255–255，完全不透明。
- 白底合成后黑色像素占比约 17.4%。中心行黑色连续段宽度均为 6 像素；第二份新会话原图的中心行白色间隔为 4、14、24 像素，显示源图本身有分离的小点。
- 使用临时环境中的 `zxing-cpp` 解码成功，且解码内容与同一响应的 `loginUrl` 完全相同。未修改项目依赖或锁文件。

因此，至少本次服务器返回的原图就有“点稀疏”的外观，不能把这种外观直接归因于 HA。解码成功也说明分离点本身不意味着二维码数据损坏。不过未拿到用户当时的原图或截图，尚不能排除页面缩放进一步影响手机扫码。

探测环境显式使用系统 CA 包 `/etc/ssl/certs/ca-certificates.crt`，保持 TLS 证书校验开启。

## 固定版本来源

- [A — Xiaomi-cloud-tokens-extractor：扫码流程与原图展示](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor/blob/c4db715dace9806e905153c2977608873e8ab7c9/token_extractor.py#L605)，重点 L635–690、L782–823。
- [B — homebridge-miot：创建扫码会话](https://github.com/merdok/homebridge-miot/blob/f2fe5d45ade480db933647a196f22999ab97080b/lib/protocol/MiCloud.js#L73)，重点 L73–108。
- [C — homebridge-miot：图片元素和链接](https://github.com/merdok/homebridge-miot/blob/f2fe5d45ade480db933647a196f22999ab97080b/homebridge-ui/public/index.html#L89)，重点 L89–99、L742–766。
- [D — 小米官方：OAuth 配置流程](https://github.com/XiaoMi/ha_xiaomi_home/blob/75b3acb6325a3a8d295688b13904737d6738a6a5/custom_components/xiaomi_home/config_flow.py#L434)，重点 L434–506。
- [E — ESPHome 第三方组件：扫码字段、图片下载与终端渲染](https://github.com/dentra/esphome-components/blob/289817000db42a6e78700cd0a7e92bbca8ac4e6e/components/xiaomi_account/xiaomi_cloud.py#L66)，重点 L66–126、L257–304。
- [F — ESPHome 第三方组件：禁用 Dashboard 文本二维码](https://github.com/dentra/esphome-components/blob/289817000db42a6e78700cd0a7e92bbca8ac4e6e/components/xiaomi_account/__init__.py#L151)。
- [G — XiaomiGateway3 云客户端](https://github.com/AlexxIT/XiaomiGateway3/blob/65f7249901803b40ef0c58e717754f5220c9f273/custom_components/xiaomi_gateway3/core/xiaomi_cloud.py)。
- [H — hass-xiaomi-miot 云客户端](https://github.com/al-one/hass-xiaomi-miot/blob/6df83d6722538466578dbb548873ec2c71739cd2/custom_components/xiaomi_miot/core/xiaomi_cloud.py)。
