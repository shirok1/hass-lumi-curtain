# 从米家云读取已绑定窗帘的 GATT LTMK

复核日期：2026-10-29。目标：已经在米家绑定并校准的 `lumi.curtain.hmcn02`。本次只检查原厂代码和已有云客户端源码，没有读取真实账号凭据、请求用户设备、连接蓝牙或更改生产配置。

## 结论

**原厂 App 有直接读取已有 GATT LTMK 的路径：登录绑定账号后，向设备所在地区发送 `POST /v2/blemesh/query_dev`，再取成功响应的 `gatt_ltmk`。** 这是正常 `bleMeshConnect` 的前置步骤，不必重新配对、重置或重做校准。[A、B]

最短实验是复用本机 `ha-cuk-ble` 已有的扫码登录和加密云请求代码，选定新窗帘的 DID，调用这个接口，校验返回值为 64 个十六进制字符，再用本项目现有 BLE 登录验证。**静态调用链已经查通；尚未证明新窗帘的实际账号、地区、权限和固件下请求及认证成功。**

## 集成实现状态

2026-10-29：集成配置流程已加入扫码登录、地区选择、设备筛选、LTMK 查询和 BLE 验证，支持新建、蓝牙发现与重新认证。首次实现面向设备绑定账号，不包含 `permitLevel == 9` 的家庭共享导入。账号会话只在配置期间使用；模拟云端和 BLE 测试已通过，真实账号与新窗帘仍待实测。下文的静态证据与实验步骤保留供复核。

## 原厂请求与响应

请求参数作为名为 `data` 的 JSON 字符串交给原厂云请求层，不是未认证的裸 JSON HTTP 请求。`BleKeyValuePair` 构造器固定键名为 `data`。[A]

按现有云客户端的地区 URL 规则，`cn` 完整地址为 `https://api.io.mi.com/app/v2/blemesh/query_dev`。签名前参数形如 `data={"did":"目标设备的完整 DID"}`；实际发送由客户端加密 `data`，并附加 `rc4_hash__`、`signature`、`ssecurity`、`_nonce` 与认证 cookies，不能直接把明文示意当作可用 curl 请求。[F]

普通路径的 JSON 为：

```json
{"did":"目标设备的完整 DID"}
```

这里的“普通路径”指下述附加条件不成立的代码分支；本人新设备落入该分支仍是待验证前提，不是本次已检查的账号状态。

还有一个精确的条件分支：`fp9.Oooooo(JSONObject, Home)` 在 `Home != null && home.getPermitLevel() == 9` 时加入：

```json
{"did":"目标设备的完整 DID","homeId":123456789,"uid":"该家庭的 owner uid"}
```

上例均为占位示意。`homeId` 是从 `home.getId()` 转出的 Long，`uid` 直接来自 `home.getOwnerUid()`；实际序列化类型应保留原始数据。字段拼写是 `homeId`，不是 `home_id`。不满足上述条件时辅助函数直接返回，不加这些字段。不能把 `uid` 当成随意指定的授权身份，也不能仅凭常量 9 断定全部共享权限语义。[C]

响应调用链已核对到通用解析器：[B、D]

```text
o00000.onSuccess(case 6)
  → rd.OooOo00
  → s21.run(case 1): 检查 code，优先 result 对象，否则根对象
  → tc1.OooO0oO: JSON 转字符串
  → rx0.o000o0o(case 2): optString("gatt_ltmk")
  → security.OooO0O0: 缓存并启动 w31 BLE 登录
```

因此，常规解密后的响应结构应按下面的契约读取；这是客户端接受的结构，不是本次抓到的真机响应：

```json
{"code":0,"result":{"gatt_ltmk":"64 位十六进制长期密钥"}}
```

原厂解析器先检查非零 `code`，失败时使用 `message`；如果没有对象类型的 `result`，则以根对象作为结果。`rx0` 缺字段或解析失败会得到空字符串，后续回调以 `-15` 结束。自己的实验客户端应要求明确的成功状态、对象结构和有效密钥，不能把 HTTP 200 当作成功。不要打印完整响应。[B、D]

## 缓存为什么可能掩盖云查询

`security.OooO0OO.OooO0o0(mac, did, token, options, callback)` 先读 `sa1.OooOoo0(mac)` 和 `sa1.OooOOO0(mac)`。只有缓存 token 与传入 token 不同，或缓存 LTMK 为空，才请求 `query_dev`；否则直接把缓存的十六进制 LTMK 转成 bytes 给 `w31` 登录。[A、E]

查询成功后，`security.OooO0O0.o000o0o()` 分别用 `sa1.OoooOoO(mac, token)` 和 `sa1.Oooo0oO(mac, ltmk)` 保存，再启动 BLE。`sa1` 展示旧存储与新存储的读取、写入和迁移；查询路径保存的是单独的 LTMK，而首次绑定回调 `z31` 保存完整绑定结果属于另一条路径。[B、E]

这说明 App 已能控制设备时不一定每次都会出现云请求。也说明设备列表里的 `token` 与 `gatt_ltmk` 是两个不同材料；不能把前者直接填进本集成 LTMK 字段。上述函数没有建立云密钥的固定过期时间，不能据此承诺解绑、重新绑定或换账号之后仍然有效。

## 可执行的最小实验方案

已有本地模块 `/home/shiroki/code/ha-cuk-ble/custom_components/cuktech_ble/lib/xiaomi_cloud.py` 提供 `start_qr_login()`、`wait_for_qr_scan()`、`list_devices()` 和 `_encrypted_post()`。该文件是可复用的客户端实现，不是小米官方 API 规范；本次没有执行其在线流程。[F]

1. 用 `start_qr_login(session)` 创建二维码，由设备绑定账号扫码；`wait_for_qr_scan()` 返回会话凭据，留在本地内存中。
2. 用实际米家地区调用 `list_devices(session, auth, region)`。按 `model == "lumi.curtain.hmcn02"` 缩小范围，再以 MAC 和用户选中的设备核对 DID。新旧窗帘可能同型号，不能直接选列表第一台。
3. 构造上述 `data`。首个实验优先使用实际绑定该设备的本人账号、对应家庭；若进入 `permitLevel == 9` 的上下文，应带正确的 `homeId`、家庭 owner `uid`。现有 `list_devices()` 不提供完整家庭权限流程，这种情况须另行补家庭枚举，不能猜字段值。
4. 调用现有签名请求函数：

   ```python
   response = await _encrypted_post(
       session,
       auth,
       region,
       "/v2/blemesh/query_dev",
       json.dumps(payload, separators=(",", ":")),
   )
   ```

   这里 `payload` 来自第 3 步。此片段展示现有函数的调用接口，不是已经执行或交付的独立脚本。
5. 要求 `code == 0`，优先取对象类型的 `result`，否则按原厂兼容路径取根对象。要求 `gatt_ltmk` 是 `[0-9a-fA-F]{64}`；规范化成小写并转为 32 bytes。本项目 `config_flow.py:137` 与 `transport.py:182` 已分别约束该格式与长度。[G]
6. 使用现有 `transport.Session.login()` 完成 GATT 鉴权，再读必要状态，验证这把密钥属于选定的 MAC。鉴权成功后才能称为可用的获取路径；不发送开合、校准或注册命令。
7. 仅记录地区、脱敏后的目标标识、返回码、字段是否存在、密钥长度和鉴权结果。完整账号会话、设备列表、LTMK 不进入聊天、Git 或调试日志。确认功能后再决定是否做 HA 扫码导入入口。

`_encrypted_post()` 已负责区域 URL、nonce、签名、RC4 请求参数、认证 cookies 和响应解密；不要另写一套签名算法。其返回的是完整解密 JSON，业务 `code` 和字段校验仍需要由查询调用者完成。现有实现将参数交给 aiohttp 的 `params=`，复用实验时不应额外打印最终 URL。[F]

## 尚未验证的边界

- 2026-10-29 服务端是否仍允许该账号在所选地区读取新设备的密钥，以及是否存在验证码、扫码风控或权限限制。
- 本人、共享设备、`permitLevel == 9` 家庭的权限差异；字段构造已确定，但服务端授权行为未实测。
- 首次绑定后的同步延迟、离线设备是否仍返回、解绑或重新绑定后的密钥轮换规则。
- 本机可复用扫码模块能否在当前会话中成功登录，以及返回的密钥能否通过新窗帘的 BLE 鉴权。

如果查询失败，应先分别检查登录、地区、DID、家庭权限及返回码；不应自动转入 `/auth`、`/bind` 或设备重置。首次注册研究另见 [ltmk-provisioning.md](ltmk-provisioning.md)。

## 证据与复核锚点

原厂 APK 版本沿用已有研究的米家 11.8.707，官方来源为[小米应用商店](https://app.mi.com/details?id=com.xiaomi.smarthome)。APK SHA-256：`60faf083a7f1874423b77d034d2fbd11ff7f97aafeb656f35333dbd5e498ac28`（沿用已有记录，本次未重算 APK）。本次重算 DEX：

- `classes9.dex`：`7aa3a4cbe6bab1e10bb1c84824a6292d5f02dddeddd4eacc2f7cc39ce6bdf423`
- `classes4.dex`：`d5d527207f54df5d0c6cd9d3b482de32473b798347b339dfd3eea2dc4c4abf2d`

以下 A、B、D、E 的 Java 根目录为 `/tmp/mihome-transport-jadx/sources/`；混淆类名需结合 DEX 哈希识别。临时文件不是长期依赖，复核时应从对应 APK 重建。

| 证据 | 原厂源码位置与范围 |
| --- | --- |
| A：入口、请求、缓存分支 | `kotlinx/coroutines/flow/core/server/CoreApiStub.java:1244`；`kotlinx/coroutines/flow/core/server/internal/bluetooth/security/OooO0OO.java:220–262`；`…/blecore/BleKeyValuePair.java:13–16` |
| B：取字段、缓存、登录 | `_m_j/rx0.java:142–149`；`…/security/OooO0O0.java:37–64` |
| C：家庭附加字段 | `classes4.dex` 的 `_m_j.fp9.Oooooo(JSONObject, Home)`；本次单类输出 `/tmp/ltmk-fp9.java:1071–1080` |
| D：响应 envelope | `classes4.dex` 的 `_m_j.o00000.onSuccess(String)`，本次 `/tmp/ltmk-o00000.java:582–590`；`_m_j/rd.java:275–282`；`_m_j/s21.java:131–170`；`_m_j/tc1.java:480–482`；`XmPluginHostApi.RecordTypeResult` 定义为 `result` |
| E：缓存存取 | `_m_j/sa1.java:262–275`、`:522–551`、`:665–681`、`:806–820` |
| F：可复用云客户端 | `/home/shiroki/code/ha-cuk-ble/custom_components/cuktech_ble/lib/xiaomi_cloud.py:115`、`:233–247`、`:250–330`、`:333–356` |
| G：本集成校验及认证 | `custom_components/lumi_curtain/config_flow.py:137`；`custom_components/lumi_curtain/transport.py:180–183`，以及 `derive_keys()`、`_authenticate()` |

关键 Java 工件 SHA-256：

| 工件 | SHA-256 |
| --- | --- |
| `security/OooO0OO.java` | `c15eed5f0c4a741cf3197be34dde1489f51df902d70661aff4112e8aae913802` |
| `security/OooO0O0.java` | `0ed5dba7188e31382bfcbbffe0c395810127ad873d7dca002ca53afb68425bd4` |
| `_m_j/rx0.java` | `4aeac73d511a879b9b911ac4522af3f8c0d7531b78ae448bd0fbb1bd9de6e61f` |
| `/tmp/ltmk-fp9.java` | `6f191fe68706a803b261f2c5be2ac1611f7738431b49b8ee3dbea8a6a90a9e89` |
| `/tmp/ltmk-o00000.java` | `16cc5ee850316a75da43131d7237c7e5b9895a0cba849151acc37d940598a286` |
| 复用的 `xiaomi_cloud.py` | `c45a3c1194cb51f6942246e46a794d72249b4443a783b6649dc00f49592a3810` |

补齐缺类时，先用 DEX `class_defs` 索引确认 `fp9`、`o00000` 位于 `classes4.dex`，再用 JADX 1.5.6 的 `--single-class` 生成输出。直接读 APK 会遇到 `assets/A3AEECD8.dex` 解析错误，针对主 DEX 则可取到相关方法；`o00000` 单类命令仍报告一项反编译错误，所以本文只引用已输出且可追踪到后续类的 `onSuccess` 分支，不声称整类无误。附加字段与响应链均为静态反编译证据，尚未做这些新位置的 IDA 指令级交叉验证。
