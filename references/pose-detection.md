# T/A-pose 识别与统一

## 默认流程

`scripts/pose_detection.py` 已接入 `auto_rig.plan_rig` 与 `convert_rig`，综合入口 `prepare_model` 自动执行，无需用户事先声明 T-pose 或 A-pose。

1. 在源模型上只读识别 Rest 姿态，写入 `rig-plan.json` 的 `pose_detection`。
2. 创建单用户工作副本，消除预检已确认的极小 Pose 数值残差。
3. 重新测量副本；符合目标模板时保留现状，不旋转、不烘焙基础网格或形态键。
4. 需要校正时调用 `apose_template.apply_template`，按当前到目标的四元数差值求解；同步烘焙基础网格、全部相对形态键及 Rest。
5. 重新读取烘焙后的 Rest，确认目标方向与掌面误差均不超过默认 0.5°，将检测详情留在返回结果的 `apose` 中（不保存独立报告），再进入 PMX 骨架整理。

识别与统一本身不导出 PMX，不处理材质或物理。特殊结构报 `needs_adaptation`，不强行套姿态。

## 判断依据

通过实际 VRM Humanoid 映射找到双上臂、前臂、手，以及食指/中指/小指根部，用关节 head 连线计算方向，不使用可能被重定向的绘制 tail 或内部骨名。角色 X 轴来自右肩到左肩，Z 来自 hips→head 去除 X 投影，Y=Z×X。检测不依赖世界展示旋转、角色绝对尺寸或 `.L/.R` 命名。

在该基底中，方向为 (x,y,z)。下放角为 `atan2(-z, sqrt(x*x+y*y))`；向前/后离开手臂侧向平面的角度单独记录。左右上臂与前臂分别测量，不能只平均左右角度。掌面使用食指/小指根部平面法线在手方向正交平面内的投影。

几何分类是工作阈值，不是行业强制定义：

| 输出 | 判断 |
|---|---|
| `t_pose` | 两侧上臂/前臂均接近水平，下放角绝对值 ≤10° |
| `a_pose` | 两侧上臂/前臂下放角位于 15°～65°，或完整目标模板匹配 |
| `intermediate` | 在自动处理范围内，但处于上述类别之间 |
| `asymmetric` | 左右分类不同，或对应臂段下放角差 >15° |
| `unsupported` | 臂段向内、明显抬高/垂直下垂、过度前后伸展或肘明显弯曲，进入适配 |

自动处理包络：臂段侧向朝外，下放角 -15°～75°，离开侧向平面 ≤35°，上臂与前臂夹角 ≤45°。这些值只决定自动支持范围与诊断分类；最终 A-pose 验收始终与所选模板比较，不靠放宽分类阈值通过。

`matches_target_apose` 需双侧上臂、前臂、手的方向误差和双掌面误差全部 ≤0.5°。例如 25° 下放的模型可归为 A-pose，但仍会校正到本 skill 约 41° 的目标模板；完全符合模板的输入才走 `kept_existing_apose`，重复运行保持网格和全部 key 原样。

## 独立接口

```python
from pathlib import Path
import runpy
pose = runpy.run_path(str(Path(SKILL_DIR)/'scripts/pose_detection.py'))
diagnosis = pose['detect_pose'](ARMATURE, HUMANOID_MAPPING)  # 只读 Rest
# 若要只读检查当前 Pose，而非 Rest：posed=True。
result = pose['normalize_apose'](
    WORK_ARMATURE, WORK_MESH_NAMES, HUMANOID_MAPPING,
    work_copy=True)
```

`detect_pose` 返回 `classification`、分侧角度、`matches_target_apose`、各段/掌面误差与建议动作。纯 Python 的 `classify_directions(captured, template)` 可用于离线几何测试。`normalize_apose` 返回检测前/后结果、动作、姿态应用和烘焙报告。自定义目标通过明确传入 `template` 支持；综合转换默认使用附带模板。

默认检测 **Rest/绑定姿态**。Pose mode、Object mode 是界面模式，不能作为 T/A-pose 判断依据。Rest 是 T、当前动作把手臂摆成 A，不等于已固化的 A-pose：自动转换仍要求静态中立 Pose，非零真实 Pose、驱动或约束需先用求值一致的适配流程处理，不能只清空 Pose 丢掉外观。

`apose_mode='auto'` 为默认；旧 `template` 选项兼容同一识别流程。`already_verified` 仍要测量并符合模板才允许继续，不能作为跳过姿态校验的开关。
