# API 参数与成本（实测 2026-09，模型 3.1）

> 仅在报错提示契约变更、或要换模型时再查官方文档；不要每次运行都重新检索。
> 官方：SubmitHunyuanTo3DProJob 文档 / ViewImage 数据结构（含 `ViewImageBase64` 字段）。

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

- model 必须 3.0/3.1；face_count 整数且 3000–1500000
- front 必填；视角数 ≤8；3.1 专属视角需 model=3.1
- 图片存在性、magic bytes（png/jpg/webp）、体积 ≤6MB、分辨率 128–5000
- base64 总量 ≤6MB

## 已验证配方（可直接复制）

```bash
# A. 三视图手办（推荐）
python scripts/multiview_3d_driver.py init --front f.jpg --back b.jpg --left l.jpg
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
