# 绘图

图在文档里的位置、图注和图后说明见 SKILL.md 共用写法·格式第 7、10、13 条。本文件讲图本身：方案、样式、图源、各类图的写法、尺寸、看图和转成 Visio。

## 方案

一张图一个 HTML 图源，用 Mermaid 12.3.0 离线渲染：库文件放在 `diagrams/assets/`，双击 HTML 即可在浏览器中离线查看。用本机 Chrome 把图截成 2 倍像素密度的 PNG，放在 `images/`，文档嵌入 PNG。布局不理想时改方向、拆图或减少元素；渲染出的 SVG 保持原样，不手工修改。

## 样式

黑白印刷风格的 UML。`diagram.js` 设定 base 主题、classic 外观、黑白配色、无阴影，字体依次为 PingFang SC、Microsoft YaHei、SimSun；`diagram.css` 把活动图起点画成实心圆、终点画成牛眼。时序图的注释框是浅黄底，其余都是黑白。样式只来自 `diagrams/assets/` 下的这两个文件，全部图因此风格一致：图源里不写 `<style>`、`%%{init}`、`classDef`、`style`、`linkStyle`（check_docs.py 会报）。

## 图源

- 模板：复制 `<skill>/assets/diagram-template.html`，只改 `<title>`、`<h1>` 和 `<pre class="mermaid">` 里的 Mermaid 文本。页面由标题、一个 `.diagram` 容器和两行脚本引用组成；截图只截 `.diagram` 中 Mermaid 输出的 svg 元素：它与 SVG 视框、即 Visio 页面同大，生成 Word 时截图作 Visio 对象的预览图，在 Word 中双击编辑后图不变形。
- `<title>`、`<h1>` 写图名，不带图号（图号会重编）。图名与文档里的图注去掉图号后相同。
- 命名：`<前缀>-<章节号，点换成短横线>-<英文短名>.html`，如 `srs-2-1-2-field-types.html`、`design-1-4-architecture.html`；PNG 与图源同名。前缀见 SKILL.md 的文档类型表。章级的图用章号，如 `srs-3-model.html`。
- 图中文字：只用中文名称和允许的领域缩写。节点 ID 用 ASCII，中文写在标签里；英文标识符、类名、表名、接口路径、文件名都不进图（check-diagram-text.mjs 会报）。

## 选图

默认按下表选，内容需要时可以换成更能说明问题的图。

| 文档 | 内容 | 图 |
|---|---|---|
| 需求规格说明 | 系统上下文、角色、功能项 | 用例图 |
| 需求规格说明 | 流程 | 活动图 |
| 需求规格说明 | 生命周期 | 状态图 |
| 需求规格说明 | 领域概念 | 概念类图 |
| 需求规格说明 | 运行环境 | 部署图 |
| 需求规格说明 | 功能组成 | 包图式的功能分解 |
| 设计方案 | 总体架构、模块组成、界面组成 | 组件图 |
| 设计方案 | 部署 | 部署图 |
| 设计方案 | 数据 | 概念数据关系图（类图，中文实体与属性） |
| 设计方案 | 交互 | 时序图：参与者是角色、工作台、各业务服务、外部系统，消息写业务含义 |
| 设计方案 | 流程 | 活动图 |
| 设计方案 | 状态 | 状态图 |

## 各类图的写法

下列写法都渲染验证过。每种图在 `<skill>/assets/examples/` 下有一个同名示例：复制到文档的 `diagrams/` 目录下（那里有 `assets/`），按命名规则改名、改写成本项目的图后再渲染。示例里的名称（包括“Model”这类英文词）取自一个接口协议管理工具项目，改写时全部换成本项目的内容。

构造型的两种写法：

- 在 Mermaid 语法位置（用例图参与者后、类图类体内）写成 HTML 转义形式 `&lt;&lt;system&gt;&gt;`、`&lt;&lt;enumeration&gt;&gt;`。直接写 `<<...>>` 会被浏览器当成标签，解析失败。
- 在标签文字里用书名号式字符 «component»、«system»、«actor»、«device»、«execution environment»、«package»。

长标签用 `<br/>` 手动换行（尺寸要求见下文“尺寸”）。

### 用例图（usecase.html）

- `usecase-beta`，`direction LR`；参与者在右侧更清楚时用 `direction RL`。
- 参与者 `actor designer("协议设计人员")`；外部系统 `actor tool("接口转换工具") &lt;&lt;system&gt;&gt;`。
- 系统边界 `systemBoundary sys("<软件名称>")` …… `end`，用例写在其中。
- 关联 `designer -- uc_create`；包含 `uc_create ..>:include uc_check`；扩展 `uc_ext ..>:extend uc_base`（由扩展用例指向基用例）；泛化 `designer --|> user`（由子角色指向父角色）。
- 标签里换行要用反引号字符串 `` uc("`第一行<br/>第二行`") ``；普通引号字符串里的 `<br/>` 会原样显示。

### 活动图（activity.html）

- `flowchart TD`。
- 起点 `start@{ shape: sm-circ }`，终点 `done@{ shape: fr-circ }`。
- 动作用圆角框 `pick(选择已发布的协议)`。
- 判断与合并用空菱形 `state{" "}`，守卫写在出边上 `state -->|"[尚无 Model]"| gen`；分支汇合时也经过一个空菱形。
- 分叉、汇合条在当前样式下显示为空心条，不用；需要表达并行时，在图后说明里交代。

### 类图（class.html）

用于需求规格说明的概念类图和设计方案的概念数据关系图。

- `classDiagram`，`direction LR` 或 `TB`。
- 概念类用中文名 `class Field["协议字段"] { 字段名称 字段类型 }`，属性一行一个，写中文名，不写类型。
- 属性的取值用“｜”列出：`接口类别：外部｜内部`。
- 枚举类在类体首行写 `&lt;&lt;enumeration&gt;&gt;`，取值一行一个；抽象类写 `&lt;&lt;abstract&gt;&gt;`。
- 可以有概念操作，如 `确定成员类型()`。
- 关系标多重性与名称：组合 `Protocol "1" *-- "1..*" Field : 包含`，关联 `--`，有向关联 `-->`，依赖 `..>`，泛化 `Node <|-- Software`。
- 关系连成一条链时，LR 会排成一行、TB 会排成一列，图过宽或过高。调整关系的书写方向（核心实体写在关系左侧），让核心实体在上层、其余实体分层展开。

### 时序图（sequence.html）

- `sequenceDiagram`，首行 `autonumber`。
- 角色写 `participant u as «actor»<br/>协议设计人员`（用 actor 人形会压住名字）；外部系统写 `participant sut as «system»<br/>被监控软件`；工作台、业务服务直接写名称 `participant proto as 协议建模`。
- 消息写业务含义：请求 `->>`，返回 `-->>`；补充说明用 `Note over ui: ……`；分支 `alt 条件 …… else 条件 …… end`，可选 `opt 条件 …… end`。

### 状态图（state.html）

- `stateDiagram-v2`；状态用中文名 `state "草稿" as Draft`，可用复合状态 `state "协议版本" as PV { …… }`。
- 迁移标签写“事件[守卫]”，守卫用半角方括号，与活动图一致：`Draft --> Latest : 发布[无错误且有变化]`。标签可以只有守卫，可以用 `<br/>` 换行。
- 状态内的活动写 `Draft : 编辑并自动保存`。
- 按条件进入不同状态时用选择伪状态：`state route &lt;&lt;choice&gt;&gt;`，再从它引出只带守卫的迁移 `route --> Borrowed : [设备在库]`。
- 状态少、排成一行过扁时，去掉 `direction LR`，按默认自上而下排列。

### 组件图（component.html）

- `flowchart TB` 或 `LR`。组件节点首行写 «component»：`ui["«component»<br/>工作台<br/>浏览与导航、建模编辑"]`；角色 `user(["«actor»<br/>用户"])`；外部系统 `sut["«system»<br/>被监控软件"]`。
- 组件内的部件用 `subgraph` 包起来，标题写 «device» 或 «component»；数据库、文件存储用 `db[("建模数据库")]`。
- 连线名称写交换的内容：`ui -- 请求与应答 --> access`；读取导出文件这类间接关系用虚线 `-. 导出文件 .->`。
- 子图多、连线压住子图标题时改成 `LR`。

### 部署图（deployment.html）

- 默认 `flowchart LR`；自上而下排版时连线容易压住子图标题。
- 运行节点只有两三个时 LR 会过扁，改用 TB。TB 下不在子图里再套子图（内层框会超出外层框），执行环境写进部件标签，如 `ui["«component»<br/>工作台（浏览器）"]`。
- 运行节点用 `subgraph`，标题写 «device» 或 «execution environment»，如 `subgraph server["«device» 服务器"]`；节点里的部件首行写 «component»；数据库用 `[(...)]`。
- 子图的左右顺序与声明顺序相反：要从左到右排列 A、B、C，就按 C、B、A 声明。

### 功能分解图（没有单独的示例）

需求规格说明 1.8 的功能组成图，写法同组件图：

```text
flowchart TB
  subgraph p22["«package» 2.2 功能域二"]
    f221["2.2.1 功能项一"] ~~~ f222["2.2.2 功能项二"]
  end
  subgraph p21["«package» 2.1 功能域一"]
    f211["2.1.1 功能项一"] ~~~ f212["2.1.2 功能项二"]
  end
```

每个功能域一个 `subgraph`，标题写 «package» 和章节号；功能项是其中的节点，用不可见连线 `~~~` 串起来，在框内自上而下排列。功能域之间没有连线时横向排开，左右顺序与声明顺序相反，所以按反序声明；功能域多、图过宽时，在子图外用 `~~~` 把上一行某个功能域的末项连到下一行功能域的首项（如 `f216 ~~~ f231`），被连的功能域就排到下一行。渲染后看图调整。

## 尺寸

原则：图放进页面宽度后，字要看得清。

- 一张图的用例或类约 12 个以内，时序图参与者约 7 个以内，超出就拆图。
- 长标签用 `<br/>` 手动换行，按词断行：用例图的椭圆里每行约 9 个字以内，其他图每行约 12 个字以内；自动折行会把词拆开（如“版／本管理”）。
- 宽度宜在约 1100 CSS 像素以内，最宽不超过约 1500；宽高比大于约 2.2 的过扁的图，换方向或拆图。render.mjs 会打印每张图的宽高，超限时警告。

## 看图

每张 PNG 都要看过，查文字溢出、重叠、截断、连线交叉、过宽过扁。图多时先用 contact_sheet.py 拼总览图（每页 4 张）整体看一遍，再逐张看改过的图的原尺寸。看图发现的问题回到图源里改，重新渲染后再看。

## 转成 Visio

用户要可编辑的图时做这一步（可选）。依赖与截图相同（playwright-core、Chrome），另需 Python 3（只用标准库）。中间目录放在本地临时目录，不放进文档目录。

```text
node <skill>/scripts/visio/export-svg.mjs <文档目录>/diagrams <中间目录> [文件名关键字]
python3 <skill>/scripts/visio/html_to_vsdx.py <中间目录> -o <文档目录>/vsdx
python3 <skill>/scripts/visio/check_vsdx.py <文档目录>/vsdx <中间目录>
```

1. export-svg.mjs 在 Chrome 中打开每个图源，把渲染结果压平成静态 SVG，每张图写一个同名 HTML。可以用浏览器打开核对，或用 render.mjs 截图后与 `images/` 对照。遇到没有登记的箭头样式时报错退出；新增箭头样式时在 export-svg.mjs 的 `MARKER_TYPE_MAP` 中按画出的形状登记。
2. html_to_vsdx.py 转成 `vsdx/<同名>.vsdx`：方框是原生形状，方框里居中的文字写进形状，两端落在方框上的线是粘连的连接线。
3. check_vsdx.py 检查包结构、关系、形状编号、连接引用和页面尺寸。装有 libvisio 的 `vsd2xhtml` 时，可以另外把 .vsdx 渲染出来，与截图并排查看，发现明显错误。

已知取舍（交付时告诉用户）：

- 不建组合：用例图的人形、类图的成员文字、连线标签、数据库圆柱是独立的形状、线条或文字框，在 Visio 中移动方框时不跟随。人形与圆柱不是方框，连到它们的线是普通线条，不粘连；同一方框上的自关联线也是普通线条。
- 曲线连线写成多段折线；在 Visio 中移动形状后，粘连的连接线按直角重新布线。
- 文字按渲染结果逐行写入。Visio 用微软雅黑显示，字形宽度与截图的苹方略有差异，个别文字框可能需要拉宽。
- 箭头对应 Visio 内置线端：实心三角、空心三角、空心菱形。Visio 没有实心菱形线端，组合关系用空心菱形线端并把连线填充设为线色。时序图的消息序号是实心小圆形状，序号文字在圆内。
- 转换结果还没有在 Visio 中实测过，实心菱形、线端大小等效果请用户在 Visio 中确认并反馈。
