# HMCN02 原厂起床唤醒模式

2026-10-23 本地只读研究。未连接蓝牙、发送控制命令或干预计数器 soak；下述设备行为尚未实机验证。

## 已确认的插件行为

原厂插件的中文说明为：窗帘从设定时间开始，**在 35 分钟内逐渐打开至 100%**。英文说明一致。当前找到的页面没有速度、持续时间或目标百分比参数，因此证据支持一个预设唤醒模式，不能据此承诺任意速度或任意时长。[A:113121、113648]

`onOpenWakeUp()` 打开原厂定时页面，传入：

```text
identify = miBleCurtainSunriseMode
onMethod = set_properties
onParam  = [{did: 当前设备, siid: 2, piid: 7, value: true}]
showOnTimerType = true
showOffTimerType = false
showPeriodTimerType = false
```

这里 SIID/PIID 来自页面传入的 `GetSpecObjByKey('wake_up_mode')`，其 HMCN02 型号 spec 明确是 **SIID 2 / PIID 7，bool，可读、可写、可通知**。spec 的 `desc` 误用 `Identify`，但属性名、调用点及文案相互对应，不能只凭这个描述把它解释为识别闪灯动作。[A:124940–124960、127251、127261；B:100]

**能确认的是：原厂定时配置的动作是写 `2.7=true`。** 插件没有在上述页面配置 35 分钟内多次调整位置的动作。推断一次写属性可能启动固件内部渐开，但“直接通过本集成 BLE 写该值会立即启动”仍待实机确认；`false` 是否取消、触发后是否自复位、状态通知如何变化，现有代码均不足以证明。

## 定时由谁执行？

`LHMiServer.openTimerSettingPageWithOptions()` 直接委托米家宿主的 `Service.scene.openTimerSettingPageWithOptions()`。未走本插件中的 BLE 定时上传逻辑。[A:58604–58606]

没有 Mesh 网关连接时，唤醒入口会提示需要网关并允许继续；页面还检查行程是否已设置，未设置时提示先校准。共享设备账号会隐藏此设置入口。[A:124963–124984、127236–127269、127647]

因此，这条配置明确使用**米家宿主场景/定时系统**，原厂 UI 把网关作为依赖提示；但仅凭插件，不能确定定时记录与执行器究竟位于云端、网关还是二者协作，也不能证明设备中保存了每天的唤醒计划。若 HA 将来在自己的自动化时刻发送一次唤醒属性，这是另一种调度实现，不应描述成已复刻原厂定时存储。

## 相关 MIOT 标识与范围

| 标识 | 型号 spec 定义 | 与唤醒的关系 |
| --- | --- | --- |
| `2.7` | `wake_up_mode`，bool，R/W/notify | 原厂唤醒定时动作写 `true`；取消与重复触发行为未知 |
| `2.1` | `motor_control`，uint8，只写；0 暂停、1 打开、2 关闭、3 Auto | 普通定时打开/关闭分别写 1/2；`Auto=3` 的具体作用未确认，不能当成速度参数 |
| `2.2` / `2.6` | 目标位置 uint8 R/W/notify / 当前位置 uint8 R/notify | 本集成已按 0–100 使用；原厂唤醒动作没有附带这两个值 |
| `2.3` | 状态 uint8；0 停止、1 打开中、2 关闭中 | 可用于后续观察唤醒运动，但未验证唤醒期间的具体状态序列 |
| `3.8` | `run_time`，uint8，只读/notify | 不是可写渐开时长；单位与唤醒的关系未在此 spec 定义 |
| `3.20` | `sync_utc`，uint32，只写 | 插件写入 `floor(Date.now()/1000)`，即 Unix 秒；不能单独证明唤醒定时保存在设备上 |
| `3.21` | `light_auto_timezone`，uint32，R/W/notify | 光感联动的有效时间段，非时区偏移；打包为起始时、分、结束时、分四个字节 |
| `3.22` | `light_auto_param`，uint32，R/W/notify | 光感联动：低 7 位星期掩码、bit 7 启用、bits 8–15 为 0–100 目标位置；不是渐开速度 |
| `2 / AIID 1` | `motor_calibrate` | 电机校准；不是唤醒动作 |
| `3 / AIID 1,2,3` | 同步全部只读属性、阻止自动设行程点、恢复自动设行程点 | 未发现与唤醒速度相关的参数 |
| `4 / AIID 1` | `identify` | 独立识别动作，与 `2.7` 的命名描述混淆应分开看 |

uint8/uint32 以上是存储类型，不代表完整有效值范围已经获得。HMCN02 型号 spec 未提供专门的 `speed` 属性或可配置唤醒时长；bundle 中大量 `fanspeed` 字符串来自通用 UI，不能据此认为窗帘支持调速。[B；A:125079–125098、122346–122354、134238–134239、134535–134546、134761–134842]

光感联动 `3.21/3.22` 在插件中经 `setSpecValueWithTarget()` 写入设备；其编码和唤醒定时属于不同功能。`3.21` 的整数布局为 `fromHour<<24 | fromMin<<16 | toHour<<8 | toMin`；`3.22` 的目标值被钳制到 0–100。星期位的日历起点仍应核对 UI 与固件，不在本文猜测。

## 对现有集成的影响与未验证事项

当前 [spec.py](../custom_components/lumi_curtain/spec.py) 的可写项仅有 `2.1` 和 `2.2`；[protocol.py](../custom_components/lumi_curtain/protocol.py:34) 的 `set_payload()` 固定编码 uint8。因此新增 `2.7` 不能只扩大允许值列表，还需按 MIOT **bool** 类型编码，并决定如何暴露一次触发与可读状态。本文没有修改实现。

后续若获授权验证，应先确认 `2.7` 的读取结果和类型，再单独验证触发、暂停/关闭对唤醒的影响、重复触发及断连后是否继续。35 分钟是否固定于所有固件、固件在低电量/行程未设置时的拒绝行为，目前均未知。没有实机证据时不把“命令 ACK 成功”等同于“35 分钟渐开已验证”。

## 本地一手证据

- **A：原厂插件 bundle**：[main.bundle](/home/shiroki/scale-re/Plugin_1025799/ios/main.bundle)。SHA-256：`799d4bf4bd7961536bb298983bd43fd32dabc8794277f81b59a370d03023208e`。上述行号按本文件文本行计数；函数包括 `onOpenWakeUp`、`OpenWakeUpPage`、`configSyncUtc`、`serializeLinkageTimeData`、`serializeLinkageConfigData`。光感页面 `LHLightLinkagePage` 是 Metro 模块 **12335**。原始文件为 indexed RAM bundle，含模块分隔 NUL，不应按普通单模块源码解释。
- **B：原厂型号 spec**：[lumi_curtain_hmcn02.json](/home/shiroki/scale-re/Plugin_1025799/ios/assets/projects/com.lumi.curtain/Main/Common/Spec/lumi_curtain_hmcn02.json)。SHA-256：`089941eb5aefb7c77b09ac577dc1ad486cba63d298453782779b09f9d9f1bcf0`。这是插件的型号映射文件，本文件未含 MIOT instance URN。主会话另行核对的[公开 MIOT 实例](https://miot-spec.org/miot-spec-v2/instance?type=urn:miot-spec-v2:device:curtain:0000A00C:lumi-hmcn02:2:0000C817)标识为 `urn:miot-spec-v2:device:curtain:0000A00C:lumi-hmcn02:2:0000C817`；本文插件调用链依据仍以固定哈希的本地文件为准。

本文仅保存公开协议标识与插件逻辑，不包含设备密钥、账号凭据或真实定时记录。
