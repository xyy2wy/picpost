# CLAUDE.md

## 项目概述

Semi-Utils 是一个批量照片水印/边框处理工具，支持 CLI 和 Web（Streamlit）两种使用方式。核心功能是读取照片 EXIF 信息，根据用户配置的布局模板自动添加水印、Logo、阴影、白边等效果后输出。

- 作者：@leslievan
- 许可证：Apache 2.0
- 外部依赖：ExifTool（通过 install.sh 安装）

## 快速命令

```bash
# 安装依赖
pip3 install -r requirements.txt

# 初始化（下载 ExifTool 等）
chmod +x install.sh && ./install.sh

# CLI 运行
python3 main.py

# Web 前端运行
streamlit run web_app.py

# 打包（PyInstaller）
pyinstaller main.spec              # 通用
pyinstaller build_win_pkg.spec     # Windows 发布包
```

## 项目结构

```
semi-utils/
├── main.py                  # CLI 入口，菜单循环 + 图片处理调度
├── init.py                  # 配置加载、菜单组装、处理器实例化
├── config.yaml              # 用户配置文件（公共接口，key 名不可随意改动）
├── processing_service.py    # 处理管线：构建 ProcessorChain、批量处理图片
├── web_app.py               # Streamlit Web 前端
├── ai_preset_service.py     # AI 助手（多服务商：OpenAI/Gemini/自定义，生成预设/文案/AI 选图/风格标签建议）
├── color_service.py         # 调色/滤镜/自动增强/防盗文字水印（纯函数）
├── cover_service.py         # 首图文字卡片（封面图）生成
├── xiaohongshu_service.py   # 切图/九宫格、拼图、页码、多比例导出（纯函数）
├── selection_service.py     # 选片：缩略图下采样、选片状态模型、过滤、导出选中（纯函数）
├── style_pack_service.py    # 全维度风格包：schema/序列化/导入导出/校验/默认补齐（纯函数）
├── preview_service.py       # 实时预览：基于下采样 + 现有处理链渲染样图（纯函数）
├── annotation_service.py    # 图上标注：气泡/纯文字/价格标签文字贴纸（纯函数）
├── compose_service.py       # 长图拼接 / 前后对比图（纯函数）
├── smart_crop_service.py    # 智能裁图：纯 PIL 显著性 + 可选 OpenCV 人脸，失败回退居中裁切（纯函数）
├── publish_service.py       # 发布草稿打包：顺序命名图片 + caption.txt（目录/ZIP）（纯函数）
├── xiaohongshu_cli.py       # 小红书多图工具的 CLI 交互处理
├── gen_video.py             # 将 output 图片合成视频（需 ffmpeg）
├── utils.py                 # 工具函数：EXIF 读取/写入、图片缩放/拼接/裁切
├── entity/
│   ├── config.py            # Config 类：读写 config.yaml，提供各项配置的 getter/setter
│   ├── image_container.py   # ImageContainer：封装单张图片 + EXIF 数据
│   ├── image_processor.py   # 处理器链（Composite 模式）：所有布局/效果处理器
│   └── menu.py              # CLI 菜单组件（Menu / SubMenu / MenuItem）
├── enums/
│   └── constant.py          # 常量定义：元素名称/值映射、位置常量、颜色
├── fonts/                   # 字体文件
├── logos/                   # 厂商 Logo 图片
├── input/                   # 待处理图片放置目录
├── output/                  # 处理结果输出目录
├── output_web/              # Web 模式上传处理的输出目录
├── output_xiaohongshu/      # 小红书工具输出目录（含 draft/ 发布草稿）
├── app_data/                # Web 前端持久化数据（自定义预设、处理历史）
├── logs/                    # 运行日志
├── install.sh               # macOS/Linux 初始化脚本
├── install.ps1              # Windows 初始化脚本
└── requirements.txt         # Python 依赖
```

## 架构要点

### 处理管线（Composite / Chain of Responsibility）

`processing_service.py` 中的 `create_processor_chain(config)` 根据当前配置动态组装处理器链：

1. **ColorGradingProcessor** — 可选，调色/滤镜/自动增强（放最前，先调色）
2. **ShadowProcessor** — 可选，添加阴影
3. **布局处理器**（互斥，由 `layout.type` 决定）：
   - `WatermarkLeftLogoProcessor` / `WatermarkRightLogoProcessor`
   - `DarkWatermarkLeftLogoProcessor` / `DarkWatermarkRightLogoProcessor`
   - `CustomWatermarkProcessor`
   - `SquareProcessor` / `SimpleProcessor`
   - `BackgroundBlurProcessor` / `BackgroundBlurWithWhiteBorderProcessor`
   - `PureWhiteMarginProcessor`
4. **MarginProcessor** — 可选，添加白色外边框
5. **PaddingToOriginalRatioProcessor** — 可选，按原始比例补白
6. **UniformResizeProcessor** — 可选，统一输出尺寸（padding/crop/stretch/smart）
   - `smart` 模式调用 `smart_crop_service` 做主体/人脸感知裁切，失败回退 crop。
7. **TextWatermarkProcessor** — 可选，平铺/单点防盗文字水印（放最后，打在成图上）

每个处理器继承 `ProcessorComponent`，实现 `process(container: ImageContainer)` 方法。

### 纯函数 service 层（CLI / Web / 预览共用）

`selection_service` / `style_pack_service` / `preview_service` / `annotation_service` /
`compose_service` / `smart_crop_service` / `publish_service` 均为纯函数服务模块：输入
PIL.Image、输出新 PIL.Image，不修改/不关闭入参，由 CLI、Web、实时预览三处共用，杜绝实现分叉。

- 实时预览（`preview_service`）与正式处理（`processing_service`）共用 `create_processor_chain`，保证视觉一致。
- 智能裁图（`smart_crop_service`）作为统一尺寸的一种裁切策略接入 `UniformResizeProcessor`。
- AI 选图、风格/标签建议收敛在 `ai_preset_service`，复用其多服务商/下采样/重试/成本预估机制。
- 重依赖（OpenCV）通过延迟导入 + 能力探测封装，缺失时优雅降级，绝不阻断模块导入或核心流程。

### 配置系统

- `Config` 类读写 `config.yaml`，提供类型安全的 getter/setter。
- 配置 key 名视为公共接口，修改需谨慎。
- Web 前端通过 `build_runtime_config()` 支持运行时覆盖配置而不修改文件。

### EXIF 处理

- 通过 `exiftool` 命令行工具读取/写入 EXIF。
- `utils.py` 中的 `get_exif()` 和 `insert_exif()` 封装了调用逻辑。
- `ImageContainer` 在初始化时解析 EXIF 并提供格式化的水印文本。

## 编码规范

- Python 3，4 空格缩进，PEP 8 风格
- 函数/变量：`snake_case`；常量：`UPPER_SNAKE_CASE`
- 模块名小写（如 `entity/image_processor.py`）
- 中文注释和用户提示是正常的，项目面向中文用户
- 保持 `config.yaml` 中的 key 名不变

## 测试

- 目前无自动化测试套件
- 验证方式：将样片放入 `input/`，运行 `python3 main.py`，检查 `output/` 结果
- 如需添加测试，放在 `tests/` 目录下，使用 pytest（`test_*.py`）

## 提交规范

- 格式：`feat: ...` / `fix: ...` / `chore: ...`
- 每个 commit 聚焦一个逻辑变更
- PR 需包含：变更说明、手动验证步骤、布局相关变更附截图

## 关键依赖

| 包 | 用途 |
|---|---|
| Pillow | 图片处理核心 |
| PyYAML | 配置文件读写 |
| tqdm | CLI 进度条 |
| python-dateutil | 日期解析 |
| requests | 网络请求 |
| streamlit | Web 前端 |
| openai | AI 助手（兼容 OpenAI 接口，可对接 Gemini） |
| opencv-python (可选) | 智能裁图的人脸优先检测；缺失时回退纯 PIL 显著性裁切 |
| ExifTool (外部) | EXIF 读写 |
