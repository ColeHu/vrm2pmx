# vrm2pmx

用于 Blender 模型制作的 Codex skill：将已导入的 VRM 骨架和基础材质整理为 mmd_tools 可编辑的数据，供人工继续修改并导出 PMX。

## 功能

- 识别 T-pose、A-pose 等绑定姿态，统一到目标 A-pose，同步处理蒙皮和相对形态键。
- 整理 MMD 人体骨架、腿脚 IK、腰与腰取消控制骨，添加腕捩、手捩及分配骨。
- 人体内部骨名使用 `.L/.R`，保留 PMX 标准日文语义，整理骨骼集合和显示枠。
- 保留足先 EX 的脚趾关节支点，设置骨长及 IK 解算后的变形顺序。
- 将原始贴图路径绑定到 MMD 主纹理槽，扩散色设为 `(1,1,1)`、反射色为 `(0,0,0)`、环境色为 `(0.5,0.5,0.5)`。
- 在独立工作副本中处理，保留源数据；自动关闭副本的三角化、已识别的按角度平滑及轮廓预览修改器。

本工具完成 Blender 内的制作准备，不自动导出 PMX，不制作刚体、Joint、描边、Toon 或 Sphere 效果。转换后仍需人工检查蒙皮、服装变形、表情和导出结果。

## 环境

- Blender，已启用 VRM 插件和 mmd_tools。
- 通过 Blender MCP 使用时，需要可连接的 Blender MCP 服务。
- 可选贴图烘焙需要带 NumPy、Pillow 的主机 Python；默认绑定原图不需要额外纹理处理环境。

## 使用

**推荐在使用本 skill 前备份原始 Blender 工程（`.blend`）及其贴图文件，保留一份可恢复的副本。**

将本目录放入 Codex 的 skills 目录，目录名保持为 `vrm2pmx`。在 Blender 中导入 VRM，确认目标骨架和绑定网格，然后向 Codex 提出请求，例如：

```text
使用 $vrm2pmx 处理当前选定的 VRM 骨架和基础材质，完成后留在 Blender 中供我修改，不导出 PMX。
```

也可以在 Blender Python 中调用 `scripts/prepare_model.py` 的 `prepare_model()`。参数和完整流程见 [SKILL.md](SKILL.md)。

默认不保存新的 `.blend`，也不保存整体准备、骨架、姿态检测三个独立 JSON 报告；检查结果由函数返回，其他计划与回测记录按流程保留。

## 目录

- `SKILL.md`：执行流程与范围。
- `scripts/`：骨架、姿态、材质和验证脚本。
- `references/`：骨骼规则、模板参数及制作方法。
- `tests/`：单元测试与 Blender 集成测试。
- `agents/`：Codex skill 展示配置。
