# HMCN02 的 LTMK 能否由集成直接建立？

复核日期：2026-10-23。范围：原厂 HMCN02 插件、米家 11.8.707 APK 的注册与登录实现、小米公开 Mesh 接入文档及第三方保存的设备 SDK 源码。未操作实机、重置设备或调用绑定接口。

## 结论

**可以研究由 HA 完成原厂云参与的配对，让用户不再手工提取 LTMK；目前没有证据支持仅靠 HA 离线生成一把新 LTMK，并让已绑定的 HMCN02 接受它。** 两者是不同的目标。

在已核对的 Mesh 注册流程中，客户端从 `/v2/blemesh/bind` 的响应读取 `gatt_ltmk`，而不是在本地随机产生这个值。注册还交换设备证书、服务端证书、公钥和签名。这证明原厂 App 的该路径依赖云端返回材料；**不能据此证明服务端如何生成 LTMK，也不能证明所有固件都不存在其他注册方式**。[证据 A、B]

后续找到的设备 SDK 源码补全了设备侧长期密钥派生和证书验证逻辑，但尚未确认 HMCN02 使用同版固件。它是可用的协议参考，尚不是现成的 HA 配对客户端。[证据 F]

现有集成的 ECDH/HKDF 是已有 LTMK 的会话登录：LTMK 是输入，输出是短期会话密钥。因此，复用登录代码或随机产生 32 字节不能自动完成设备侧的长期凭据安装。[证据 C]

## 已确认的原厂流程

| 阶段 | 直接证据 | 判断与置信度 |
| --- | --- | --- |
| 开始 Mesh 注册 | `BleMeshRegisterConnector`，反编译类 `securitychipauth/OooO0o.java`，常量 `OooOoo = 40 00 00 00`，`OooOOOo()` | 注册与现有 `50` 登录是不同流程；高 |
| 设备身份材料 | 同类 `OooOOo(int, byte[])`，分支 1 接收设备证书，分支 3 拆出 `dev_info[12]`、`manu_cert_id[8]`、设备公钥 `[64]` | App 不只交换一把随机密钥；高 |
| 云认证 | 上述分支 3 请求 `POST /v2/blemesh/auth`，包含 `pdid`、`dev_mesh_pub`、`manu_cert_id`、`dev_cert`、`dev_info`，以及字面字段 `code="123456"` | 该字面值不能直接解释为用户 PIN 或绕过认证的口令；高 |
| 返回设备验证材料 | `OooO0OO.o000o0o()` 读取 `sign`、`pub`、`server_cert`、`did`；依次发送 parcel 类型 7、3、8 | 原厂路径把服务端证书、公钥和签名送给设备；高 |
| 云绑定 | `OooO0o.OooOOo()` 分支 4 接收设备签名后请求 `POST /v2/blemesh/bind`，带 `pdid`、MAC、`sign`、`did` 等 | 云认证和云绑定是两次请求；高 |
| 获得长期材料 | `_m_j/z31.o000o0o()` 默认分支解析 `static_oob`、`gatt_ltmk`、Mesh 地址和 `appkey`；空 LTMK 会失败，成功响应交给 `sa1.Oooo()` 保存 | LTMK 在此来自绑定响应；服务端生成/派生方式未知；高 |
| 下发 Mesh 配置 | `OooO0o.OooOoO0()` 的本地 `SecureRandom` 只产生 16 字节，写入 provision TLV 类型 1；配置再用 `static_oob` 加密下发 | 这段随机值属于 Mesh 配置，不能当成 32 字节 GATT LTMK 的生成器；高 |

以上 Java 路径的共同前缀见证据 A。注册最终的设备确认与配置下发仍需成功；仅保存 `/bind` 的返回值不等于设备已完成配对。

## 配对资格、重置、OOB 与证书

原厂 HMCN02 插件中的 `LHSpecHandleUtils.CheckConnectionType()` 只选择网关或已经连通的 BLE Mesh 通道；它没有展示首次添加设备的完整状态机。型号 spec 中 `dev_factory_reset` 定义为 `siid=3, eiid=3, type=event`，因此不能把这个条目当成可调用的重置动作。[证据 D]

小米公开开发文档说明 Mesh SDK 已实现配网等协议功能，首次绑定会关联模组证书链、PID 和 MAC；重置操作由具体产品自行定义。这支持继续检查设备身份与注册状态，但不能推出 HMCN02 的具体按键时长或跨账号解绑规则。[证据 E]

`static_oob` 在上述原厂路径由云绑定响应取得，随后用于配置阶段；目前没有发现该路径要求用户手输 OOB 的证据。APK 中其他普通 BLE、安全芯片注册类的 OOB 或 LTMK 派生逻辑不能直接套用到 Mesh 窗帘。尤其 `BleSecurityChipRegisterConnector`（`Oooo000.java`）与这里的 `BleMeshRegisterConnector` 是不同实现。[证据 A、B]

插件目录中的 `bundle.cert` 和 `bundle.sign` 是插件包的签名材料，不是已证明可用于设备配对的服务端凭据。找到的 SDK 使用嵌入根公钥验证服务端证书和签名，并检查证书组织名称；这提供了设备侧信任链的参考实现。HMCN02 是否使用相同代码与信任根，仍须固件或受控实验确认。[证据 F]

## 已绑定设备直接读取

2026-10-29 补充：针对已经在米家绑定并校准的窗帘，后续研究应优先走原厂 `/v2/blemesh/query_dev` 的已有凭据查询路径。请求与缓存调用链、可复用的扫码登录代码及尚待实测的部分，见 [已绑定设备 LTMK 获取研究](ltmk-retrieval.md)。这条路径不需要实现本文的首次注册状态机；目前尚未在新窗帘上验证云端返回及 BLE 登录。

## 三条可选路线

| 路线 | 能解决什么 | 尚未验证的前提 |
| --- | --- | --- |
| HA 导入已绑定设备的凭据 | 去掉手工抓取或注入 App 的步骤；本质仍是读取已有 LTMK | 合法账号授权、目标地区 API、返回字段、家庭/共享设备权限与凭据更新规则 |
| HA 驱动云参与的首次注册 | 用户在 HA 完成发现、配对、保存长期凭据；后续使用现有本地登录控制 | 未绑定状态如何识别、是否须解绑/重置、米家登录与请求认证、证书交换、Mesh 网络配置、失败回滚、设备最终确认 |
| 完全离线建立新 LTMK | 不依赖米家云的完整注册 | 尚无可运行路径；需确认固件信任链、服务端签名与密钥计算、设备长期凭据写入与绑定状态处理 |

第二条是目前最有原厂代码依据的研究方向，但还不能称为已实现功能。第三条可以继续逆向，不能仅凭 App 中没找到算法就宣告密码学上绝对不可行。

建议未来 HA 流程：发现并识别型号 → 选择导入已有凭据或注册新设备 → 完成账号授权及设备状态检查 → 执行身份认证和绑定交换 → 等待配置与设备确认 → 用获得的 LTMK 重新登录并读取必要状态 → 成功后保存配置条目。任何可能解绑或重置的步骤都应明确展示影响；研究阶段不触发这些动作。

## 证据与复核位置

**A：新版本 APK 的直接代码。** 官方来源为[小米应用商店](https://app.mi.com/details?id=com.xiaomi.smarthome)，APK SHA-256 为 `60faf083a7f1874423b77d034d2fbd11ff7f97aafeb656f35333dbd5e498ac28`；`classes9.dex` 为 `7aa3a4cbe6bab1e10bb1c84824a6292d5f02dddeddd4eacc2f7cc39ce6bdf423`。本机 Java 根目录 `/tmp/mihome-transport-jadx/sources/`；注册类位于 `kotlinx/coroutines/flow/core/server/internal/bluetooth/security/securitychipauth/`，绑定回调位于 `_m_j/z31.java`。反编译包名有混淆/映射痕迹，须连同 DEX 哈希识别。

**B：IDA 原始指令复核。** `/tmp/mihome-ida/registration-ida.txt`、`dump-registration.py`：`z31.o000o0o@VL` 地址 `0x22bdbc`，`static_oob` 位于 `0x22bdd8`，`gatt_ltmk` 位于 `0x22bdea`，空值错误位于 `0x22be48`，保存响应位于 `0x22be7c`；`OooO0o.OooOOo@VIL` 位于 `0x5727a4`，`/bind` 常量位于 `0x572910`，`/auth` 常量位于 `0x572ab4`。地址只适用于上述 DEX 数据库，不能与旧版米家地址混用。

**C：登录与注册的区别。** 当前仓库 [protocol.md](protocol.md) 的认证章节，以及同一 DEX 中 `BleMeshLoginConnector` 的 `0x57176c`：已有 LTMK 与 ECDH 共享秘密拼接，使用 `miot-mesh-login-salt`、`miot-mesh-login-info` 做 HKDF。这里没有凭空恢复或注册长期密钥。

**D：型号插件。** `/home/shiroki/scale-re/Plugin_1025799/ios/main.bundle`，SHA-256 `799d4bf4bd7961536bb298983bd43fd32dabc8794277f81b59a370d03023208e`；`CheckConnectionType()` 约在行 120771。`assets/projects/com.lumi.curtain/Main/Common/Spec/lumi_curtain_hmcn02.json`，SHA-256 `089941eb5aefb7c77b09ac577dc1ad486cba63d298453782779b09f9d9f1bcf0`；`dev_factory_reset` 在行 344。插件版本信息见同目录 `project.json`。它支持本型号与 Mesh 控制通道的关联，不能单独证明全部配对限制。

**E：公开一手文档。** [MiEcosystem：蓝牙 Mesh 接入开发](https://github.com/MiEcosystem/miio_open/blob/master/ble/04-%E8%93%9D%E7%89%99Mesh%E6%8E%A5%E5%85%A5%E5%BC%80%E5%8F%91.md) 的“模组 + 固件SDK”和“创建蓝牙 Mesh产品”。文档所链接的 SDK 仓库本次未能取得，因此没有用它来推断服务端或固件侧 KDF。

临时逆向目录不是仓库内可长期依赖的工件；可移植的复核锚点是 APK/DEX 哈希、类/函数与地址。本文不包含真实设备 LTMK、账号凭据或完整绑定响应。

## 已有代码能复用到哪一步？

截至上述日期，**在本次检查的公开项目中，没有找到可直接用于 HMCN02、完整实现云参与首次注册的独立客户端**。找到的是设备 SDK、原厂 App 反编译代码、通用米家云请求组件，以及使用已有 LTMK 的登录 PoC；这不代表其他地方一定不存在实现。

| 项目/代码 | 已实现的部分 | 与第二条路线的差距 |
| --- | --- | --- |
| [XiaomiGateway3 的 `MiCloud`](https://github.com/AlexxIT/XiaomiGateway3/blob/65f7249901803b40ef0c58e717754f5220c9f273/custom_components/xiaomi_gateway3/core/xiaomi_cloud.py#L241) | 异步米家账号登录、会话和签名请求；MIT | 适合作为 HA 云请求层参考；没有完整 Mesh 注册状态机 |
| [hass-xiaomi-miot 的云请求层](https://github.com/al-one/hass-xiaomi-miot/blob/a3a07b8504bacfee114a6052ae864d7dd149bf71/custom_components/xiaomi_miot/core/xiaomi_cloud.py#L1045) | 异步 API、签名/RC4 请求；Apache-2.0 | 与自身 HA 辅助代码耦合；仍需编写注册协议与配网流程 |
| [Squachen/micloud](https://github.com/Squachen/micloud/blob/dc002b87fc09ef36199715cfa10f1eaa2d2d4210/micloud/micloud.py#L271) | Python 账号登录与通用区域请求；MIT | 同步请求组件，没有 Mesh 注册流程；异步 HA 需处理调用方式 |
| [kabbi 的 Yeelight 登录 PoC](https://gist.github.com/kabbi/242a48dde4d3ae548f6d54cf1f987776#file-connect-js) | `0x50`、ECDH、HKDF 和 AES-CCM 登录 | `cloudKey` 必须预先填入设备的 32 字节 LTMK；不执行 `/auth`、`/bind`，不是首次注册实现；页面未见显式许可证 |
| [xiaomi-diffuser-ble](https://github.com/clairefiorettiythzr6802-eng/xiaomi-diffuser-ble) | 另一型号的本地 GATT 登录、属性控制与 HA MQTT 桥；项目标示 MIT | README 要求从 `/v2/blemesh/query_dev` 取得已有 `gatt_ltmk`；不能据此证明支持新设备注册 |
| [旧版米家 `fhc.java` 镜像](https://github.com/argahsuknesib/CyberSecurity-Summer-School-2021/blob/39cbcbe3c59f241cebf36c2acd65fff202849b22/RiskInDroid/AndroidApp/xiaomi/sources/_m_j/fhc.java) | 原厂云接口调用的反编译参考，含 Mesh 接口 | 不是独立实现或可直接安装的库；公开可读不等于有权直接复制原厂代码 |
| [Telink SDK 中 `mible_mesh_auth.c`](https://github.com/bomingfeng/TLSR825x_SIG_Mesh/blob/3479a4c310d825b8316f6664070a0377ac08daa5/example/Telink_mesh/mesh/mi_api/certi/mesh_auth/mible_mesh_auth.c) | 设备端注册、证书验证、长期密钥派生与登录；`mi_api` 子树附 Apache-2.0 | 可核对设备行为；不是米家账号/云接口客户端，且未验证对应 HMCN02 固件 |

kabbi 的[原始认证记录](https://gist.github.com/kabbi/32658d7d3a086cd47d877882933a9908) 同样明确把云端 LTMK 作为会话派生输入。因此，“有 Mesh authentication 代码”不能直接解释成“已有创建新 LTMK 的配对代码”。这里仅调查代码，没有运行这些第三方项目或验证其当前账号登录成功率。

**F：设备侧 SDK 的新增证据。** 固定快照 `3479a4c310d825b8316f6664070a0377ac08daa5` 的上述 `mible_mesh_auth.c`：`mesh_reg_msc()` 验证服务端证书/签名；`mesh_reg_auth()` 检查 `Mijia Cloud` 组织名称，并使用以下派生。`eph_key` 是该次 ECDH 共享秘密，OOB 取决于协商结果；此处主 `LTMK` 与供 GATT 登录的 `GATT_LTMK` 是两个值。

```text
LTMK      = HKDF-SHA256(eph_key, salt=空或OOB[16],
                        info="miot-mesh-auth-info", length=32)
GATT_LTMK = HKDF-SHA256(LTMK, salt="gatt-ltmk",
                        info="miot-mesh-ltmk-info", length=32)
static_oob = SHA256(LTMK) 的前16字节
```

原始文件定位：根公钥验证约 690 行，签名验证约 713 行，组织名称检查约 816 行，主密钥派生约 837–851 行，GATT 密钥派生约 896–899 行。相同快照的 [`mi_api/LICENSE`](https://github.com/bomingfeng/TLSR825x_SIG_Mesh/blob/3479a4c310d825b8316f6664070a0377ac08daa5/example/Telink_mesh/mesh/mi_api/LICENSE) 为 Apache-2.0；此处只确认该子树的许可文件，没有把整个第三方 SDK 仓库视为同一许可证。

实现第二条路线时，最有用的组合是：通用异步云请求层负责账号与请求认证，现有集成复用 BLE 路由及分片能力，参照原厂注册状态机和设备 SDK 新增首次注册。仍需补上账号授权界面、`0x40` 证书交换、`/auth` 与 `/bind` 的完整调用、Mesh 配置下发、设备确认和失败恢复；目前没有已验证的现成依赖可以一次覆盖这些步骤。
