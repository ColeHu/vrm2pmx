---
name: vrm2pmx
description: 在 Blender 内把 VRM 人体骨架与基础材质整理为 mmd_tools 可编辑的 PMX 制作数据，保留蒙皮和形态键，验证后交给人工修改与导出；适用于模型制作中的骨架或材质提效；自动关闭副本的三角化/按角度平滑，保留相对形态键；不执行 PMX 导出、物理或渲染效果制作。
---

# vrm2pmx

将已导入 Blender 的 VRM 模型整理为可继续制作的工作副本。完成点是 Blender 内的 MMD root、人体骨架、IK、基础材质与检查报告。**不调用 PMX 导出器，不生成临时 PMX，不以导出回读作为本阶段完成条件。** 后续人工编辑与导出由用户接手。

## 自动处理修改器与形态键

“三角化 / 按角度平滑 + 相对形态键”属于本 skill 已支持的正常输入，不得仅凭它们的存在要求用户手工适配或提供另一份模型。使用本 skill 自带的 `prepare_model.py`，预检列出已知修改器后继续创建副本，并在副本关闭其视口/渲染开关，再同步处理基础网格与全部相对形态键。不直接对带形态键的网格 Apply 修改器，不删除形态键；保持 Armature 蒙皮修改器启用。脚本均从当前 skill 目录解析，不绕过综合入口直接烘焙仍启用修改器的原模型。

源 `.blend` 位于工作区之外不妨碍内存中的副本转换。把新副本、检查点和报告写到当前可写工作区，不覆盖原文件，也不把无法覆盖源目录当作停止理由。只有实际脚本报告未知修改器、动画/绝对形态键等尚未支持的具体问题时，才对该项做局部适配；不能笼统说“几何节点或形态键不支持”。

## 范围与默认值

- 人体内部名称统一使用 `.L` / `.R`，如 `足首.L`、`足先EX.L`，同步顶点组、约束、显示枠和内部映射；PMX 标准日文语义单独保存在 name_j。足先EX保留原脚趾关节头部支点，尾部水平朝前并略微超过同侧つま先、つま先ＩＫ头部。见[人体名称与足趾支点](references/body-names-and-toe-pivot.md)。
- 骨架：读取 VRM Humanoid，先判断当前 Rest 是 T-pose、A-pose、中间姿态还是左右不对称，再统一到标准 A-pose。已经符合目标模板的 A-pose 保持原样；需要校正时同步处理 Rest、蒙皮和全部相对形态键。随后建立基础 MMD 人体、腰与左右腰取消控制骨，以及左右腕捩/手捩系统；保留附属骨、原比例，按扭转计划重分配副本局部权重，设置集合和显示枠。
- 材质：默认把当前材质的原始图片文件以绝对路径绑定到正式 MMD 主纹理槽，不重烘焙或改写原图。固定 Diffuse=(1,1,1)、Specular=(0,0,0)、Ambient=(0.5,0.5,0.5)，保留明确的 Alpha/双面设置。仅在用户明确需要合成颜色因子/遮罩、生成纯色图等处理时，选择 texture_policy='bake'；不把复杂 shader 的视觉等价作为原图绑定完成条件。
- 不制作轮廓：新材质 `enabled_toon_edge=False`、`edge_weight=0`；不读取/转换描边遮罩，不改 `mmd_edge_scale`。不生成或指定 Toon、Sphere/MatCap、Rim、PBR 或自定义 shader。
- 默认按参考比例添加或复用 `腰`、`腰キャンセル左`、`腰キャンセル右`，并添加次标准插件“腕弯曲/手弯曲”对应的 `腕捩`、`手捩` 与分配骨；不构建刚体、Joint、Spring Bone，不完整迁移 VRM expression/动画驱动，不自动添加 D 骨等其他扩展骨。
- 输出只包含工作场景、报告、新贴图，以及按需保存的新 `.blend` 副本。保留源对象与源贴图，不覆盖原项目。
- 骨骼分类完成后自动删除工作副本中已无骨骼的旧集合，递归清理全空分支；保留有非空子集合的父级、新分类和有效插件辅助集合，并同步内部集合计划。源骨架集合不变。

## 工具与执行

需要 Blender、已注册的 VRM 插件与 mmd_tools；通过 Blender MCP 或 Blender Python 执行脚本。默认原图绑定只使用 Blender Python；可选烘焙的纹理 worker 才使用主机 Python 的 NumPy/Pillow。从当前已注册插件确定实际模块名；多套 mmd_tools 同时载入时显式选择活动模块，不硬编码安装位置。见 [工具与环境](references/tools.md)。

先确认用户要处理的实际骨架、绑定网格及目标范围。有多个候选且没有明确指向时，读取名称、绑定关系和可见状态后让用户选择，不默认处理整个文件。

默认复用固定脚本，不逐骨、逐材质重写算法：

```python
from pathlib import Path
import runpy

api = runpy.run_path(str(Path(SKILL_DIR) / 'scripts/prepare_model.py'))
report = api['prepare_model'](
    SOURCE_ARMATURE, OUTPUT_DIR,
    mmd_package=ACTIVE_MMD_PACKAGE,
    texture_python=HOST_PYTHON,
    texture_policy='original_file', # 默认原始文件路径；显式 'bake' 才生成处理贴图
    parts='both',       # 'rig' 表示只处理骨架
    save_blend=False)  # 如需持久化交付，True 保存新的 prepared.blend 副本
```

入口先预检，在唯一 `prep-…` 目录记录计划，创建独立场景与单用户骨架/网格，先禁用副本上的三角化、已识别的按角度平滑及 MToon 轮廓预览修改器，再转换、回测并检查材质；成功后切到工作场景并选中骨架。源名称、目标名称和场景从报告读取，不猜 `.001` 后缀。只转换已有工作副本的材质时，读 [基础材质流程](references/materials.md)，使用 `convert_materials` 后调用 `validate_materials`。

## 流程与方法

1. **检查输入。** 识别 VRM 0.x/1.0 与 Humanoid，检查实际绑定、Rest/Pose、矩阵、修改器、UV、材质有效来源及相对形态键。`pose_detection.py` 只读测量双臂关节方向和掌面，生成姿态类型、角度、目标误差及建议动作，见 [姿态识别与统一](references/pose-detection.md)。缺失映射列出具体语义；可显式传 `mapping`/`version`。脚本支持范围和例外见 [骨架方法](references/rig.md)。
2. **建立工作副本并取消指定修改器。** 所有变换在独立场景进行，骨架和网格 data 单用户。预检遇到 `TRIANGULATE` 或已确认的 `Smooth by Angle` 几何节点组时，列入自动处理清单，继续创建副本；转换前在副本同时关闭它们的视口与渲染开关，包括仅渲染开启的情况，不要求用户先手动处理。保留修改器数据以供人工恢复，不应用到网格、不删除原对象上的修改器；源开关不变。按角度平滑根据节点组/资产信息与平滑节点结构识别，不能只看修改器名称就关闭任意几何节点。其他未知的启用修改器仍报告适配需求。材质创建新数据块后仅替换副本的槽，保留面分配、UV、基础拓扑、源贴图与材质共享关系。`work_copy=True` 是调用方承诺，不能替代实际复制。
3. **准备骨架。** `normalize_apose` 根据检测结果处理：符合目标的 A-pose 跳过旋转与烘焙；T-pose、其他 A-pose、中间/左右不对称姿态按当前到目标的差值校正，同步烘焙全部相对形态键，并重新测量 Rest。在返回结果中记录姿态检测，再整理标准名、控制骨、腿/脚 IK、集合和显示枠。按 [腰与腰取消控制](references/waist.md) 在上下半身起点后方略低处建立腰，按本角色髋宽适配；上下半身挂到腰，腿经左右腰取消继承下半身，使用原生 MMD `-1` 付与旋转，不把腰只当命名占位。随后按[腕与手扭转骨](references/arm-twist.md)添加左右腕捩/手捩及各三根分配骨，分配骨按参考竖直向上，主捩骨尾与下级肘/手腕头重合，配置固定轴、原生付与旋转和局部权重转移。不把“A-pose 大类”直接视为模板已通过，不重复叠加固定下放角。用 [骨架方法](references/rig.md) 理解算法，特殊语义查 [骨骼约定](references/bone-conventions.md)。
4. **准备基础材质。** 按 [基础材质流程](references/materials.md) 转换主图、Alpha、双面和固定色。默认 `parts='both'`、`texture_policy='original_file'`：复制工作材质和图片数据块，将原始文件的绝对路径写入 mmd_tools 正式主图槽，不修改共享源图片。不能因骨架通过或源 shader 在 Blender 有颜色就省略材质转换。支持有效输出上的图片、Principled、单位强度 Emission 颜色，以及一支透明/一支颜色的 Mix Shader；只追踪已连接的有效路径，忽略断开的旧节点，校验命名 UV 与导出活动 UV 一致。复杂节点、缺图或不明确 UV 进入适配清单；不通过添加效果追求 VRM 渲染观感。
5. **验证并交接。** 按 [验证与人工接手](references/validation.md) 检查结构、连续 IK、绑定保全及实际材质字段/图片引用。主贴图必须能被当前活动 mmd_tools 的 `FnMaterial.get_texture()` 读为 `IMAGE`，图片文件存在；仅在普通节点里看到贴图不算 PMX 材质完成。交付对象/场景、输出路径、检查结果和人工待办。

足先 EX 的 PMX 变形顺序也必须通过检查：足首为父级，EX 在同侧腿 IK、脚趾 IK 解算之后计算；不能仅凭 Blender 内跟随正常就交接。新转换自动调用 scripts/foot_order.py，已有工作副本可单独 repair(ARMATURE, work_copy=True)。细节见[人体名称与足趾支点](references/body-names-and-toe-pivot.md)。

## 报告输出

转换不保存 preparation-report.json、rig-report.json、pose-detection.json 这三个独立报告。综合结果由 prepare_model 返回，骨架与姿态详情分别位于 report['rig'] 和 report['rig']['apose']；仍执行全部验证，失败原因通过返回值或异常交接。不要为了交付再手工生成这三个文件。其余计划、回测及材质记录维持现有行为。

## 失败与续作

`prepared_for_manual_edit` 表示骨架与基础材质本阶段均通过，`pmx_exported=False` 始终成立。综合入口 `parts='rig'` 返回 `rig_prepared_materials_pending`，必须明确交接“仅骨架，材质及导出器贴图槽尚未准备”，不能描述成有贴图模型已完成；底层 `rig_prepared` 也只证明骨架范围。后续补材质直接对已有工作网格执行 `convert_materials` 与 `validate_materials`，不重做骨架。`needs_repair` 保留隔离副本，读取 `repair_queue` 或错误，在该副本设检查点、局部修复、重跑同一回测与材质验证后继续。不要重新复制已转换结果、重烘焙颜色或调宽容差掩盖失败。骨架工具拒绝再次转换带 `auto_rig_version` 的目标；原图绑定会验证已有原图材质的实际路径和固定颜色后才跳过；可选烘焙会跳过带签名的目标，但仍须读取实际字段验证。

不能把“Blender 内验证通过”写成“PMX 文件已验证”。默认保留 `pmx_roundtrip`、特定服装动作变形、完整表情、物理、目标渲染外观为未验证项；这些属于人工后续制作。若用户另行要求实际导出，另立导出工作范围，不修改本 skill 的默认终点。
