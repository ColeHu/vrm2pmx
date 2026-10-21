# 工具与环境

本 skill 自包含，骨架与基础材质工具均随 skill 提供。所有路径通过 `Path(SKILL_DIR)` 解析；输入骨架、网格、场景和插件模块从调用参数及实际数据读取，不依赖固定模型名或机器安装路径。

通过可用 Blender MCP 执行 bpy；也可在用户选择的 Blender Python 环境运行。显式设置 Object Mode、scene、active object 和 selection，更新依赖图后读取求值结果。不会因为使用本 skill 就启动一套替代 renderer 或改变插件全局配置。

Blender 内需要 bpy、mathutils 和 NumPy，mmd_tools 提供骨/材质属性与 MMD model API，VRM 插件提供 Humanoid 与 MToon 有效属性。通过 `bpy.context.preferences.addons`、已加载模块及实际 `__file__` 定位插件；多套包同时载入时选注册活动包。缺依赖则说明具体依赖，保留输入，不假造 API 或硬编码扩展路径。

默认原图绑定无需纹理 worker。只有显式烘焙时，纹理 worker 由主机 Python 执行，需要 NumPy/Pillow；显式传 `texture_python` 最可靠，未传则查 PATH。子进程剥离 Blender 的 PYTHONHOME/PYTHONPATH，Windows 使用隐藏进程，不向 Blender Python 安装额外图片包。

| 文件 | 职责 |
|---|---|
| `prepare_model.py` | 综合入口、源指纹、实际材质验证、新 `.blend` 副本保存与交接报告 |
| `auto_rig.py` | Humanoid 计划、副本、A-pose、人体/控制骨、IK、集合/显示枠、动作回测与局部修复 |
| `modifier_policy.py` | 只读计划三角化/已知按角度平滑的取消，在副本关闭视口与渲染开关；不应用或删除源修改器 |
| `pose_detection.py` | 只读 T/A/中间/不对称姿态判断、目标模板比较、幂等 A-pose 统一与烘焙后复测 |
| `arm_twist.py` | 次标准腕捩/手捩布局、竖直分配骨与主骨尾连接、局部摆放修复、保留其他份额的权重转移及原生动作验证 |
| `waist_tools.py` | 按角色髋宽建立参考相对腰/腰取消布局，配置原生 MMD 付与旋转并验证动作 |
| `apose_template.py` / `apose_bake.py` | 标准姿态四元数处理与普通 LBS/形态键同步烘焙 |
| `rig_audit.py` / `foot_layout.py` / `leg_ik.py` | 结构、足部几何、连续动作诊断 |
| `bone_naming.py` / `toe_pivot.py` | 人体 .L/.R 命名与引用同步；保留原 toes 旋转支点及既有副本局部修复 |
| `bone_collections.py` | 逐骨集合归属与覆盖验证 |
| `original_materials.py` | 默认原始图片文件定位、绝对路径绑定、固定色、独立材质/图片、验证与回退 |
| `auto_materials.py` | 读源、计划、贴图提取、创建/替换材质、签名与回退 |
| `blender_material_source.py` | 追踪有效图片/Principled/Emission/透明混合输出，读取遮罩与静态二维 Mapping，并校验命名 UV |
| `material_auto_pixels.py` | NumPy/Pillow 主图线性乘色、UV 重采样和 Alpha |
| `material_tools.py` | FnMaterial 创建基础材质，拒绝 Sphere/Toon/轮廓请求 |

报告中的 `unverified=['pmx_roundtrip', ...]` 是范围说明，不是要求追加导出步骤。`repair_export_axes` 仅为既有轴属性修复函数名，和文件导出无关。
