# Requirements Document

## Introduction

本规格定义 semi-utils 的「创作者工作流套件」（creator-workflow-suite）。semi-utils 是一个面向摄影爱好者的 Python 照片批处理工具，提供 CLI 与 Streamlit Web 两种前端，核心用户群体高频在小红书发布多图。

本套件在现有能力（水印/边框排版、统一尺寸、调色滤镜、防盗水印、长图切割/九宫格、拼图封面、首图卡片、多比例导出、页码角标、AI 预设助手与 AI 文案）之上，补齐从「选片 → 修图 → 排版 → 选封面 → 文案 → 打包」的完整创作链路。功能按产品优先级分为三档：

- **P0**：实时预览、全维度风格模板（"我的风格包"）、AI 智能选图与排序。
- **P1**：选片/culling 基础能力、文字贴纸/标注、前后对比图/长图拼接、智能裁图。
- **P2**：批次内多样式、发布草稿打包。

所有新功能遵循既有架构约束：纯图像逻辑放在独立 service 模块（纯函数、不修改/不关闭传入图片对象），CLI 与 Web 复用同一套 service；配置驱动且向后兼容；重型依赖可选并优雅降级；界面与提示使用中文。

## Glossary

- **System（系统）**: semi-utils 应用整体，含 CLI 前端与 Web 前端。
- **Web_Frontend（Web 前端）**: 基于 Streamlit 的 `web_app.py` 交互界面。
- **CLI_Frontend（CLI 前端）**: 基于菜单的命令行界面（`main.py` / `init.py`）。
- **Processor_Chain（处理器链）**: 责任链结构，串联多个 `ProcessorComponent`，逐个变换一个 `ImageContainer`。
- **Image_Container（图像容器）**: 单张图片的上下文对象，持有原图、EXIF 与处理中间图。
- **Service_Module（服务模块）**: 不依赖 UI 的纯图像逻辑模块（如 `color_service`、`cover_service`、`xiaohongshu_service`），函数为纯函数，不修改/不关闭传入图片对象。
- **Runtime_Config（运行时配置）**: Web 端通过 `build_runtime_config` 在不写回 `config.yaml` 的前提下临时覆盖的配置对象。
- **Style_Pack（风格包）**: 一个命名模板，打包布局、四角文字、Logo、白边、阴影、调色/滤镜、统一尺寸、防盗水印等全部可配置维度，可保存、应用、删除、导出、导入。
- **Live_Preview（实时预览）**: 在 Web 端调整参数后，对选中图片即时生成并展示处理效果，无需点击正式「处理」。
- **AI_Selection（AI 智能选图）**: 由视觉大模型从一组图片中选出最适合发布的 N 张并给出推荐顺序与理由。
- **Basic_Selection（基础筛选）**: 不依赖 AI 的本地筛选/排序，依据清晰度、亮度等可计算指标。
- **Sharpness_Score（清晰度分数）**: 衡量图片是否模糊的数值指标，采用拉普拉斯方差（Laplacian variance）等轻量算法计算，数值越高越清晰。
- **Perceptual_Hash（感知哈希）**: 对图片内容生成的指纹值，内容相近的图片其哈希的汉明距离（Hamming distance）较小，用于识别相似图（连拍）。
- **Similarity_Group（相似图分组）**: 感知哈希汉明距离小于阈值的一组图片，视为连拍/近似重复。
- **Culling（选片）**: 从大量候选图中批量筛选、标记、去重的过程。
- **Star_Rating（星级标记）**: 用户为单张图片设置的 0 到 5 的整数评分，用于筛选。
- **Subject_Saliency（主体显著性）**: 图片中视觉主体所在区域的估计，用轻量显著性算法得到，供智能裁图保留主体。
- **Smart_Crop（智能裁图）**: 在统一尺寸/多比例裁切时，依据主体显著性或人脸位置确定裁切窗口，尽量不切到主体。
- **Sticker_Annotation（文字贴纸/标注）**: 叠加在图片上的文字气泡、标签、箭头或价格标签等标注元素。
- **Comparison_Image（对比图）**: 将两张图片以左右或上下方式拼接，用于展示前后/对比效果。
- **Long_Stitch（长图拼接）**: 将多张竖向图片纵向拼接为一张长图。
- **Publish_Draft（发布草稿）**: 包含处理后图片、AI 文案与标签、顺序清单的本地文件夹产物，便于按序手动上传，不对接任何发布接口。
- **Optional_Dependency（可选依赖）**: 非必装的第三方库（如 OpenCV、人脸检测模型）；缺失时系统优雅降级，不影响既有 CLI/Web 基本流程。
- **Graceful_Degradation（优雅降级）**: 当可选依赖或 AI Key 缺失时，系统以非报错方式回退到基础方案并明确提示用户。

---

## Requirements

### Requirement 1: 实时预览（P0）

**User Story:** 作为博主，我希望在 Web 端调整参数后立即看到选中图片的处理效果，以便在不反复点「处理」的情况下快速试错样式。

#### Acceptance Criteria

1. WHEN 用户在 Web_Frontend 选择一张待预览图片并完成参数调整，THE Web_Frontend SHALL 使用当前 Runtime_Config 对该图片生成处理后预览图并展示。
2. WHEN 用户修改任一影响成图的参数（布局、Logo、白边、调色、统一尺寸、防盗水印、四角文字等），THE Web_Frontend SHALL 更新 Live_Preview 以反映修改后的参数。
3. WHERE 输入目录或上传列表中存在多张图片，THE Web_Frontend SHALL 允许用户选择用于预览的具体图片，默认选择第一张。
4. WHEN 生成 Live_Preview，THE Web_Frontend SHALL 在调用处理逻辑前将预览图最长边下采样至不超过 1600 像素，以控制单次预览的处理耗时与内存占用。
5. THE Live_Preview SHALL 复用与正式处理相同的 Service_Module 处理逻辑，使预览结果与正式处理结果在相同参数下保持一致。
6. IF Live_Preview 生成过程中发生异常，THEN THE Web_Frontend SHALL 展示中文错误提示并保留上一次成功的预览结果，且不中断当前页面其他操作。
7. WHILE Live_Preview 正在生成，THE Web_Frontend SHALL 显示处理中状态提示。

### Requirement 2: 全维度风格模板（"我的风格包"）（P0）

**User Story:** 作为博主，我希望把布局、调色、边框、水印、四角文字、统一尺寸等全部维度打包成命名风格包并一键套用，以便统一账号视觉风格并与其他博主分享。

#### Acceptance Criteria

1. WHEN 用户在 Web_Frontend 输入风格包名称并触发保存，THE Web_Frontend SHALL 将当前布局、Logo、四角文字、白边、阴影、等效焦距、按原比例补边、调色/滤镜、统一尺寸、输出质量、防盗文字水印这些维度的取值保存为一个命名 Style_Pack。
2. WHEN 用户选择一个已保存的 Style_Pack 并触发应用，THE Web_Frontend SHALL 将该 Style_Pack 中保存的所有维度的取值写入当前会话参数。
3. WHEN 用户选择一个已保存的 Style_Pack 并触发删除，THE Web_Frontend SHALL 从持久化存储中移除该 Style_Pack。
4. WHEN 用户选择一个已保存的 Style_Pack 并触发导出，THE Web_Frontend SHALL 生成一个包含该 Style_Pack 全部维度的 JSON 文件供下载。
5. WHEN 用户导入一个 Style_Pack JSON 文件，THE Web_Frontend SHALL 校验文件结构并将其加入可用 Style_Pack 列表。
6. IF 导入的文件无法解析为合法的 Style_Pack 结构，THEN THE Web_Frontend SHALL 拒绝导入并展示中文错误提示。
7. IF 用户保存或导入的 Style_Pack 名称与内置预设名称或已存在的 Style_Pack 名称重复，THEN THE Web_Frontend SHALL 提示名称冲突并要求用户更换名称或确认覆盖。
8. WHERE 导入的 Style_Pack 缺少某些维度字段，THE Web_Frontend SHALL 对缺失字段采用既有默认值补齐，确保应用后处理流程可正常执行。
9. THE System SHALL 将 Style_Pack 持久化于应用数据目录（`app_data`）中，且不修改 `config.yaml` 的既有 key。

### Requirement 3: AI 智能选图与排序（P0）

**User Story:** 作为博主，我希望从一组图片中由 AI 选出最适合发小红书的 N 张并给出推荐顺序与理由，以便快速决定发哪些图、按什么顺序发。

#### Acceptance Criteria

1. WHEN 用户提供一组图片、目标张数 N 与发布目标，并触发 AI_Selection，THE System SHALL 调用视觉大模型并返回所选图片、推荐发布顺序与每张的推荐理由。
2. THE System SHALL 在调用 AI_Selection 前将每张图片最长边下采样至不超过 1024 像素后再上传，以控制 token 成本与延迟。
3. WHEN 用户触发 AI_Selection，THE System SHALL 展示本次调用的图片数量与粗略 token 规模预估，供用户在调用前评估成本。
4. WHERE 待选图片数量超过单次批量上限，THE System SHALL 限制单次上传数量至该上限并提示用户已截断。
5. IF 未配置可用的 AI API Key，THEN THE System SHALL 回退到 Basic_Selection，依据 Sharpness_Score 与亮度对图片排序并选出前 N 张，且提示当前为非 AI 基础筛选模式。
6. IF AI_Selection 调用失败（鉴权失败、超时、限流、网络异常或返回非法 JSON），THEN THE System SHALL 返回中文友好错误提示，并允许用户回退到 Basic_Selection。
7. WHEN AI_Selection 返回的推荐张数与用户请求的 N 不一致，THE System SHALL 以用户请求的 N 为上限截断结果并向用户说明实际返回数量。
8. THE AI_Selection SHALL 通过与现有 AI 服务相同的 OpenAI 兼容接口实现，支持 OpenAI、Gemini 与自定义兼容服务商。

### Requirement 4: 选片 / culling 基础能力（P1）

**User Story:** 作为博主，我希望批量预览图片、为图片打星筛选，并获得相似图（连拍）与糊图的提示，以便快速从大量候选中保留精华。

#### Acceptance Criteria

1. WHEN 用户上传或选择一组图片，THE Web_Frontend SHALL 以批量缩略图网格展示这组图片供预览。
2. WHEN 用户为某张图片设置 Star_Rating，THE Web_Frontend SHALL 记录该图片在当前会话中的 Star_Rating（0 到 5 的整数）。
3. WHEN 用户设置最低星级筛选条件，THE Web_Frontend SHALL 仅展示 Star_Rating 不低于该条件的图片。
4. WHEN 用户触发相似图检测，THE System SHALL 使用 Perceptual_Hash 计算两两汉明距离，并将距离低于相似阈值的图片归入同一 Similarity_Group 并提示用户。
5. WHEN 用户触发糊图检测，THE System SHALL 计算每张图片的 Sharpness_Score，并对低于清晰度阈值的图片给出糊图提示。
6. THE Sharpness_Score 与 Perceptual_Hash 计算 SHALL 使用轻量算法（如拉普拉斯方差、感知哈希）实现，不强制依赖重型图像库。
7. WHERE 用户调整相似阈值或清晰度阈值，THE System SHALL 依据新阈值重新计算 Similarity_Group 与糊图提示结果。
8. THE Similarity_Group 与糊图提示 SHALL 仅作为建议呈现，由用户决定是否剔除，THE System SHALL NOT 自动删除任何源文件（错误处理例外）。

### Requirement 5: 文字贴纸 / 标注（P1）

**User Story:** 作为博主，我希望在图片上添加文字气泡、标签、箭头或价格标签，以便在探店、好物、穿搭等场景标注重点信息。

#### Acceptance Criteria

1. WHEN 用户在图片上添加一个 Sticker_Annotation 并指定文字内容与位置，THE Service_Module SHALL 返回叠加了该标注的新图片。
2. THE Service_Module SHALL 支持文字气泡、标签、箭头与价格标签这四类 Sticker_Annotation。
3. WHEN 用户为 Sticker_Annotation 指定字号、文字颜色与背景颜色，THE Service_Module SHALL 按指定样式渲染该标注。
4. WHEN 用户在同一张图片上添加多个 Sticker_Annotation，THE Service_Module SHALL 按用户指定的顺序与位置叠加全部标注。
5. WHERE 标注位置坐标超出图片边界，THE Service_Module SHALL 将标注夹取到图片可见范围内。
6. THE Sticker_Annotation 渲染函数 SHALL 为纯函数，不修改且不关闭传入的图片对象，使 CLI_Frontend 与 Web_Frontend 复用同一函数。
7. WHERE 文字包含中文字符，THE Service_Module SHALL 使用项目内置中文字体渲染，IF 指定字体加载失败，THEN THE Service_Module SHALL 回退到默认字体。

### Requirement 6: 前后对比图 / 长图拼接（P1）

**User Story:** 作为博主，我希望把多张竖向图片拼成一张长图，或把两张图做成左右/上下的前后对比图，以便在一张图内讲清楚变化与故事。

#### Acceptance Criteria

1. WHEN 用户提供多张图片并触发 Long_Stitch，THE Service_Module SHALL 将这些图片纵向拼接为一张长图并返回新图片。
2. WHEN 用户为 Long_Stitch 指定图片间距与背景颜色，THE Service_Module SHALL 按指定间距与背景颜色渲染拼接结果。
3. WHERE Long_Stitch 的输入图片宽度不一致，THE Service_Module SHALL 将所有图片按统一宽度对齐后再拼接。
4. WHEN 用户提供两张图片并触发 Comparison_Image，且选择左右模式，THE Service_Module SHALL 将两张图片左右并排拼接为一张对比图。
5. WHEN 用户提供两张图片并触发 Comparison_Image，且选择上下模式，THE Service_Module SHALL 将两张图片上下堆叠拼接为一张对比图。
6. THE Long_Stitch 与 Comparison_Image 函数 SHALL 为纯函数，不修改且不关闭传入的图片对象。
7. IF Long_Stitch 或 Comparison_Image 的输入图片数量不满足最小要求，THEN THE Service_Module SHALL 返回中文错误提示。

### Requirement 7: 智能裁图（主体 / 人脸识别后再裁）（P1）

**User Story:** 作为博主，我希望在统一尺寸或多比例裁切时尽量不切到主体或人脸，以便批量裁图后主体仍然完整居中。

#### Acceptance Criteria

1. WHEN 用户启用 Smart_Crop 并将一张图片裁切到目标比例，THE System SHALL 依据 Subject_Saliency 估计的主体区域确定裁切窗口，使主体尽量保留在成图内。
2. WHERE 可选的人脸检测依赖可用，THE System SHALL 在存在人脸时优先以人脸位置确定裁切窗口。
3. WHERE 主体显著性与人脸检测的可选依赖均不可用，THE System SHALL 优先使用轻量显著性方案，IF 轻量显著性方案不可用，THEN THE System SHALL 降级为居中裁切。
4. WHEN Smart_Crop 降级为居中裁切，THE System SHALL 提示用户当前为居中裁切模式及降级原因。
5. THE Smart_Crop SHALL 在不启用时保持现有统一尺寸/多比例裁切的居中裁切行为不变。
6. THE Smart_Crop 依赖（如 OpenCV、人脸检测模型）SHALL 作为 Optional_Dependency，缺失时 System SHALL 正常完成裁切流程而不报错。
7. THE Smart_Crop 输出图片尺寸 SHALL 与用户指定的目标尺寸完全一致。

### Requirement 8: 批次内多样式（P2）

**User Story:** 作为博主，我希望一组图片中封面与内页可以应用不同的配置或风格包，以便封面更吸睛、内页更统一。

#### Acceptance Criteria

1. WHEN 用户在一个处理批次中为封面图与内页图分别指定 Style_Pack 或配置，THE System SHALL 对封面图应用封面配置、对其余图片应用内页配置。
2. WHEN 用户指定哪一张为封面图，THE System SHALL 将该图片作为封面应用封面配置，默认以批次中的第一张为封面。
3. WHERE 用户未为封面指定独立配置，THE System SHALL 对全部图片应用同一配置，保持现行单一配置行为。
4. THE System SHALL 在批量处理结果中标识每张图片实际应用的配置来源（封面或内页），供用户核对。

### Requirement 9: 发布草稿打包（P2）

**User Story:** 作为博主，我希望把处理后的图片、AI 文案与标签、顺序清单打包成一个发布草稿，以便按顺序手动上传到小红书。

#### Acceptance Criteria

1. WHEN 用户触发生成 Publish_Draft，THE System SHALL 创建一个本地文件夹，包含按发布顺序命名的处理后图片、一个文案文本文件与一个顺序清单文件。
2. THE Publish_Draft 的文案文本文件 SHALL 包含标题、正文与标签内容。
3. THE Publish_Draft 的顺序清单文件 SHALL 按发布顺序列出每张图片的文件名。
4. WHERE 用户已通过 AI 文案功能生成文案与标签，THE System SHALL 将该文案与标签写入 Publish_Draft 的文案文本文件。
5. WHERE 用户未生成 AI 文案，THE System SHALL 生成包含空文案占位的文案文本文件，使草稿结构完整。
6. WHEN 用户在 Web_Frontend 请求下载 Publish_Draft，THE Web_Frontend SHALL 将该草稿文件夹打包为单个压缩文件供下载。
7. THE System SHALL NOT 调用任何小红书发布接口，Publish_Draft 仅产出本地文件。

### Requirement 10: 可选依赖与优雅降级（非功能）

**User Story:** 作为维护者，我希望所有新增的重型依赖都是可选的且缺失时优雅降级，以便不破坏现有 CLI/Web 基本流程。

#### Acceptance Criteria

1. THE System SHALL 将 OpenCV、人脸检测模型等重型库作为 Optional_Dependency，采用延迟导入。
2. IF 某项 Optional_Dependency 未安装，THEN THE System SHALL 对依赖该库的功能执行 Graceful_Degradation 并以中文提示用户，且不影响不依赖该库的功能。
3. WHILE 任一 Optional_Dependency 缺失，THE System SHALL 保持现有水印处理、调色、统一尺寸、小红书多图工具等既有 CLI 与 Web 流程可正常运行。
4. IF AI 相关功能所需的 API Key 或 openai 依赖缺失，THEN THE System SHALL 回退到对应的非 AI 基础能力并提示用户。

### Requirement 11: 配置向后兼容（非功能）

**User Story:** 作为维护者，我希望新增配置不破坏既有 `config.yaml`，以便老用户升级后配置仍可用。

#### Acceptance Criteria

1. THE System SHALL 保持 `config.yaml` 既有 key 名不变。
2. WHEN System 读取 `config.yaml` 且新增配置项缺失，THE System SHALL 通过 setdefault 机制以默认值补齐新增配置项。
3. WHEN 既有 `config.yaml`（不含本套件新增项）被加载，THE System SHALL 正常启动并使用默认值运行新功能。

### Requirement 12: 服务与 UI 解耦及资源管理（非功能）

**User Story:** 作为维护者，我希望纯图像逻辑与 UI 解耦且处理大图时妥善管理资源，以便 CLI 与 Web 复用同一套逻辑且不发生内存泄漏。

#### Acceptance Criteria

1. THE System SHALL 将本套件的纯图像逻辑实现于不依赖 Streamlit 或 CLI 菜单的 Service_Module 中。
2. THE Service_Module 中处理图片的纯函数 SHALL NOT 修改或关闭调用方传入的图片对象。
3. WHEN Service_Module 在处理过程中创建临时 PIL 图片对象，THE Service_Module SHALL 在不再需要时释放这些临时对象。
4. THE CLI_Frontend 与 Web_Frontend SHALL 复用同一套 Service_Module 函数实现等价功能。

### Requirement 13: 中文界面与提示（非功能）

**User Story:** 作为中文用户，我希望所有新功能的界面与提示均为中文，以便顺畅使用。

#### Acceptance Criteria

1. THE System SHALL 以中文呈现本套件所有功能的界面标签、按钮与说明文案。
2. WHEN 本套件任一功能向用户展示错误或提示，THE System SHALL 以中文呈现该错误或提示。
