# API 参数与成本（实测 2026-09，模型 3.1）

> 仅在报错提示契约变更、或要换模型时再查官方文档；不要每次运行都重新检索。

- 提交任务接口：`SubmitHunyuanTo3DProJob` — https://cloud.tencent.com/document/product/248/123447
- 数据结构 `ViewImage`（含 `ViewImageBase64`）：https://cloud.tencent.com/document/api/1804/120828
- 国际站同接口说明：https://intl.cloud.tencent.com/zh/document/api/1284/75540

## 请求体（驱动脚本自动构造）

| 字段 | 说明 |
|------|------|
| `ImageBase64` / `ImageUrl` | 主图（front 视角），与 `Prompt` 互斥 |
| `MultiViewImages[].ViewType` | `back` / `left` / `right`（3.0 与 3.1）；`top` / `bottom` / `left_front` / `right_front`（仅 3.1） |
| `MultiViewImages[].ViewImageBase64` | 视角图 base64（本技能主用，免图床） |
| `Model` | `3.0` / `3.1`；3.1 不支持 LowPoly / Sketch |
| `EnablePBR` | 材质增强，+10 点 |
| `FaceCount` | 3000–1500000，默认 500000 |
| `GenerateType` | `Normal`（带纹理）/ `LowPoly`（智能减面）/ `Geometry`（白模，仅 GLB，EnablePBR 无效）/ `Sketch`（草图，可与 prompt 同用） |

## 输入约束

- 格式 jpg / png / webp；单边 128–5000px
- 单张 ≤6MB；多视角 base64 总和 ≤6MB
- 每个视角限一张；合计最多 8 张
- 3.0 版本传 `top`/`bottom`/`*_front` 会被脚本拦截（`invalid-params`）

## 成本档位（先按用途选档，再提交）

| 场景 | 配置 | 点数 |
|------|------|------|
| 手办 / 主角级（默认） | 多视角 + PBR + 3.1 | **40**（Normal 20 + MultiView 10 + PBR 10） |
| 只要正面、急用 | 单图 + PBR | **30** |
| 迭代试构图 | 单图 / 多视角，不开 PBR | 20 / 30 |
| 只要几何做打印前检查 | `Geometry` 白模 | 最低档（仅 GLB，无贴图） |

## 参数校验（脚本 `check` 已实现）

硬校验（返回 `invalid-params` / `invalid-image`，阻断提交）：

- model 必须 3.0/3.1；face_count 整数且 3000–1500000
- **model 3.1 不支持 `LowPoly` / `Sketch`**（要低模用 3.0）
- `Geometry` 时不允许 pbr=true（白模无贴图，EnablePBR 无效）
- front 必填；视角数 ≤8；3.1 专属视角需 model=3.1
- 图片存在性、magic bytes（png/jpg/webp）、体积 ≤6MB、分辨率 128–5000
- base64 总量 ≤6MB

软提示（`hints`，不阻断）：

- 背景不够纯色（边框取样标准差 >40）
- 主体占画面 <50% 或疑似被裁切（>95%）

## 输入图质量清单（提交前逐条核对）

1. 画面里只有**一个主体**，无杂物、无第二个角色
2. 背景**纯色**（白/浅灰最佳），不要实景、渐变、花纹
3. 主体占画面 **>50%**，四边留白但不出画
4. 无文字标注、无水印、无 UI 元素
5. 光照均匀、不过曝不过暗；三视图的**比例与高度一致**（脚底/头顶对齐）
6. 三视图建议同一白底、同一画幅；裁剪时去掉底部"正视图"之类的文字

**预处理默认值**：bbox 裁出主体 → 贴回白色正方形画布 → 主体较长边占 70% 且居中（裁剪代码见 workflow.md 第 1 节）。

### 实测 A/B（同一角色，同为 3.1 + PBR，各 40 点）

| | A 旧裁切（三等分直裁，背景不纯、主体占比小） | B 新裁切（bbox + 白方画布 70%） |
|---|---|---|
| `check` 质量提示 | 2 条 | 0 条 |
| 顶点 / 三角面 | 328,955 / 497,654 | 323,993 / 481,508 |
| GLB 体积 | 48.1 MB | 48.7 MB |
| 主观评价 | 可用 | **更好** |

两点结论：

1. **面数不等于质量**：B 面数略低但观感更好，收益来自"主体居中 + 纯背景"带来的重建精度，不是堆面数。不要为了提质量去调高 `--face-count`。
2. 混元对"不完美输入"兜底良好——A 仍能出可用模型；追求稳定出片才需要走预处理。

## 已验证配方（可直接复制）

```bash
# 0. 预处理：bbox 裁主体 + 白色方画布（主体占 70%），见 workflow.md
# A. 三视图手办（推荐）
python scripts/multiview_3d_driver.py init --front .hy3d/inputs/front.jpg --back .hy3d/inputs/back.jpg --left .hy3d/inputs/left.jpg
# B. 单图快速验证
python scripts/multiview_3d_driver.py init --front f.jpg --no-pbr
# C. 白模（只要几何，准备打印检查）
python scripts/multiview_3d_driver.py init --front f.jpg --generate-type Geometry
# D. 低模（需 model 3.0 时）
python scripts/multiview_3d_driver.py init --front f.jpg --model 3.0 --generate-type LowPoly
```

## 输出

`ResultFile3Ds[]`：`Type`（OBJ/GLB）、`Url`（COS 签名链接，24h 有效）、`PreviewImageUrl`。
`ResultCreditConsumed` / `ResultCreditDetails` 记录本次实际扣点，写入 jobs.json。
