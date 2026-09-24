# 采访逐字稿校时 API

将 **ASR 按时间排序的识别字符**（每项含字符、起止毫秒）与 **人工修订后的完整文本**
做全局对齐（最少编辑代价 / Levenshtein），为修订文本生成**逐字时间轴**。

核心能力：

- **全局最少编辑代价对齐**，不是逐字贪心。
- **重复字符结合上下文选择匹配位置**；在同等最小编辑代价下，**优先匹配更早的识别字符原位置**。
- **新增字符**在「相邻已匹配字符」与「录音边界（0 / 总时长）」形成的空档内**均匀分配**时间。
- **删除字符不输出**。
- 输出时间段严格**按顺序**且**处于录音范围内**；每字符带 `matched`（匹配/替换）或 `computed`（推算）标记。
- 输入时间无效（负值、起止倒置、越界、乱序、重叠等）返回 **HTTP 422 + 明确中文错误信息**。
- 支持 `POST /locate-quote`：复用校时输入和逐字时间轴，在修订文本中全局选择互不重叠的近似引语命中。
- 支持 `POST /revision-diff`：复用校时输入与同一对齐结果，输出人工修订相对识别稿的有序改动块（insert/delete/replace）。
- 支持 `POST /captions`：复用校时输入和逐字时间轴，按字符数/时长上限把修订文本切成字幕段。
- 提供 `/health` 健康检查。
- 对齐内核为 **C（ctypes 调用）**，无编译器时自动回退**纯 Python** 实现，二者算法一致。

---

## 一条命令启动（Docker Compose）

```bash
docker compose up --build -d
```

启动后（首次会构建镜像并在**容器启动时安装 Python 依赖**）：

- API 地址：`http://localhost:8000`
- 健康检查：`GET http://localhost:8000/health`
- 校时接口：`POST http://localhost:8000/align`
- 引语定位接口：`POST http://localhost:8000/locate-quote`
- 修订差异接口：`POST http://localhost:8000/revision-diff`
- 字幕切分接口：`POST http://localhost:8000/captions`
- 交互式文档：`http://localhost:8000/docs`（OpenAPI：`/openapi.json`）

停止：

```bash
docker compose down
```

> 不想用 Compose，可直接：
> `docker build -t transcript-align-api . && docker run --rm -p 8000:8000 transcript-align-api`

---

## 必要配置

均通过**环境变量**配置，`docker-compose.yml` 中已给默认值：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 容器监听端口 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `MAX_RECOGNIZED_CHARS` | `20000` | 识别字符数量上限 |
| `MAX_REVISED_LENGTH` | `20000` | 修订文本长度上限 |
| `MAX_QUOTE_LENGTH` | `20000` | `/locate-quote` 引语长度上限 |
| `ALIGN_NATIVE` | `1` | `1` 优先 C 内核；`0` 强制纯 Python 后备内核 |
| `UVICORN_EXTRA_ARGS` | （空） | 透传给 uvicorn 的额外参数 |

宿主机端口映射在 `docker-compose.yml` 的 `ports`（默认 `"8000:8000"`，左侧可改）。

### 内存与规模

对齐的反向距离表是 O(m·n) 的 `uint16` 紧凑表（m=识别字符数，n=修订字符数），
峰值约为 **(m+1)×(n+1)×2 字节**，请求结束即释放。例如：

- 5,000 × 5,000 ≈ 50 MB
- 12,000 × 12,000 ≈ 288 MB
- 20,000 × 20,000 ≈ 800 MB

Compose 默认 `mem_limit: 2g`。如调大规模，请同步增大内存上限。

---

## API

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok","backend":"native"}
```

### `POST /align`

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `recognized` | 数组 | 按时间排序的识别字符；元素为 `{"char","start","end"}` |
| `recognized[].char` | 字符串 | **恰好 1 个字符** |
| `recognized[].start` | 整数 | 起始毫秒，`>=0` |
| `recognized[].end` | 整数 | 结束毫秒，`>= start` |
| `total_duration` | 整数 | 录音总时长（毫秒），非负 |
| `revised_text` | 字符串 | 人工修订后的完整文本 |

示例：

```bash
curl -X POST http://localhost:8000/align \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":400,"end":500},
      {"char":"界","start":600,"end":700}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀世界"
  }'
```

响应：

```json
{
  "total_duration": 1000,
  "chars": [
    {"char":"你","start":0,"end":100,"source":"matched","matched_index":0,"recognized_char":"你"},
    {"char":"好","start":200,"end":300,"source":"matched","matched_index":1,"recognized_char":"好"},
    {"char":"呀","start":300,"end":400,"source":"computed","matched_index":null,"recognized_char":null},
    {"char":"世","start":400,"end":500,"source":"matched","matched_index":2,"recognized_char":"世"},
    {"char":"界","start":600,"end":700,"source":"matched","matched_index":3,"recognized_char":"界"}
  ],
  "inserted_count": 1,
  "deleted_count": 0,
  "matched_count": 4,
  "backend": "native"
}
```

字段说明：

- `chars`：与 `revised_text` **等长、同序**的逐字时间轴。
- `source`：
  - `matched`：该修订字符匹配/替换到某个识别字符（继承其 `start/end`）。
    人工纠错（替换）时 `char` 与 `recognized_char` 不同，`matched_index` 为识别字符下标。
  - `computed`：新增字符，时间在相邻锚点/录音边界空档内均匀推算。
- 时间保证：`0 ≤ start ≤ end ≤ total_duration`，且相邻字符时间不回退。

### `POST /locate-quote`

在执行与 `/align` 完全相同的输入校验和逐字校时后，从 `revised_text` 中查找与
`quote` 的 **Levenshtein 编辑距离**（插入、删除、替换）不超过 `max_edits` 的连续非空片段。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `quote` | 字符串 | 待定位引语，必须非空，默认长度不超过 20,000 字符 |
| `max_edits` | 整数 | 最大编辑次数，允许 `0`、`1`、`2` |

示例：

```bash
curl -X POST http://localhost:8000/locate-quote \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"世","start":500,"end":600},
      {"char":"界","start":700,"end":800}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀世界",
    "quote": "你好世界",
    "max_edits": 1
  }'
```

响应：

```json
[
  {
    "start_index": 0,
    "end_index": 5,
    "text": "你好呀世界",
    "edits": 1,
    "start": 0,
    "end": 800
  }
]
```

字段说明：

- `start_index` / `end_index`：命中片段在 `revised_text` 中的字符下标；`end_index` 为半开区间（不含）。
- `text`：修订文本中实际命中的连续文字。
- `edits`：实际插入、删除、替换次数；仅保留 `edits <= max_edits` 的片段。
- `start` / `end`：分别取命中片段首字符时间轴的 `start` 和末字符时间轴的 `end`（毫秒）。
- 同一位置可能同时存在多个近似片段。接口会在所有候选中先选出**互不重叠**集合：
  依次最大化命中数量、最小化 `edits` 总和；仍同分时，选择下标序列字典序最小者，并按原文顺序返回。
- 没有命中时返回 `[]`。

### `POST /revision-diff`

在执行与 `/align` 完全相同的输入校验与全局对齐后，输出**人工修订相对识别稿**
的有序改动块：

- 未匹配的识别字符 → **删除**；
- 无来源的修订字符 → **插入**；
- 匹配但来源字符不同 → **替换**；
- 以**未改动的匹配字符**为边界，把连续改动合并为一块；块内同时出现识别侧与
  修订侧文字（替换或插删混合）时类型为 `replace`。

请求体与 `/align` 完全相同。

示例：

```bash
curl -X POST http://localhost:8000/revision-diff \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"吗","start":0,"end":100},
      {"char":"你","start":200,"end":300},
      {"char":"好","start":400,"end":500},
      {"char":"呢","start":600,"end":700},
      {"char":"玩","start":800,"end":900}
    ],
    "total_duration": 1000,
    "revised_text": "你好呀呢耍"
  }'
```

响应：

```json
[
  {"type":"delete","recognized_start":0,"recognized_end":1,"revised_start":0,"revised_end":0,"recognized_text":"吗","revised_text":"","start":0,"end":100},
  {"type":"insert","recognized_start":3,"recognized_end":3,"revised_start":2,"revised_end":3,"recognized_text":"","revised_text":"呀","start":500,"end":600},
  {"type":"replace","recognized_start":4,"recognized_end":5,"revised_start":4,"revised_end":5,"recognized_text":"玩","revised_text":"耍","start":800,"end":900}
]
```

字段说明：

- 按**原文顺序**返回改动块；无改动时返回 `[]`。
- `recognized_start` / `recognized_end`：块在识别字符序列中的起止下标，
  `end` 为半开区间（不含）；`revised_start` / `revised_end`：块在 `revised_text`
  中的起止字符下标，同样为半开区间。
- `recognized_text` / `revised_text`：块两侧的文字；纯插入时识别侧为空串，
  纯删除时修订侧为空串。
- `type`：`insert`（纯插入）、`delete`（纯删除）、`replace`（替换或插删混合）。
- `start` / `end`：块涉及的原识别字符与修订时间轴字符中最早的 `start`、
  最晚的 `end`（毫秒）。

### `POST /captions`

在执行与 `/align` 完全相同的输入校验和逐字校时后，按 `revised_text` 的**连续字符**
切分字幕段。每段跨度取「首字 `start` 到末字 `end`」，其**字符数**与**跨度毫秒数**
均不得超过上限；某个单字自身跨度超限（不存在任何合法切分）时返回 **422**。

请求体包含 `/align` 的全部字段，另加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `max_chars` | 整数 | 每段最大字符数，允许 `1～80` |
| `max_duration_ms` | 整数 | 每段最大时长（毫秒），允许 `500～30000` |

切分目标（依次）：

1. **段数最少**；
2. **断点总分最高**：断点左字为 `。！？；` 加 2000 分，再加上相邻字停顿毫秒值
   （左字 `end` 到右字 `start`，截到 `0～1000`）；
3. 仍同分时，取**断点下标序列字典序最小**者。

示例：

```bash
curl -X POST http://localhost:8000/captions \
  -H 'Content-Type: application/json' \
  -d '{
    "recognized": [
      {"char":"你","start":0,"end":100},
      {"char":"好","start":200,"end":300},
      {"char":"。","start":400,"end":500},
      {"char":"世","start":600,"end":700},
      {"char":"界","start":800,"end":900}
    ],
    "total_duration": 1000,
    "revised_text": "你好。世界",
    "max_chars": 3,
    "max_duration_ms": 30000
  }'
```

响应：

```json
[
  {"start_index": 0, "end_index": 3, "text": "你好。", "start": 0, "end": 500},
  {"start_index": 3, "end_index": 5, "text": "世界", "start": 600, "end": 900}
]
```

字段说明：

- 按**原文顺序**返回每段；`start_index` / `end_index` 为段在 `revised_text` 中的
  起止字符下标，`end_index` 为半开区间（不含）。
- `text` 为该段文字；`start` / `end` 分别取首字符时间轴的 `start` 与末字符的 `end`。
- `revised_text` 为空时返回 `[]`。

### 输入校验（HTTP 422）

例如 `end < start`：

```json
{"detail": "第 0 项字符 '你' 的结束时间 100 不能早于起始时间 300"}
```

覆盖：负时间、结束早于起始、超出录音总时长、未按起始时间排序、时间区间重叠、
识别字符多于/少于 1 个、字段缺失或类型错误、超出规模上限、`quote` 为空或超长、
`max_edits` 不在 0～2、`max_chars` 不在 1～80、`max_duration_ms` 不在 500～30000、
单字跨度超过 `max_duration_ms` 等。

---

## 对齐与时间分配规则（说明）

1. 以识别字符序列为源串、修订文本为目标串，求最小编辑距离。
2. 在所有最小编辑代价方案中，选择「按识别字符原位置字典序最早」的匹配：
   从前往后扫描识别字符，**只要把当前字符匹配出去仍能达到全局最优就优先匹配**；
   否则优先新增（保留该识别字符等待后续匹配），最后才删除。
   这保证重复字符结合上下文落到**更早**的识别位置。
3. 匹配/替换字符直接采用识别字符的 `start/end`。
4. 连续的新增字符视为一段，取其左右边界：
   - 左侧有锚点用左锚点的 `end`，否则用录音起点 `0`；
   - 右侧有锚点用右锚点的 `start`，否则用录音终点 `total_duration`；
   在该整数区间内做均匀切分（端点固定、地板法取整，保证有序且不越界）。
5. 被删除的识别字符不出现在结果中。

---

## 本地开发（不用 Docker）

需要 Python 3.11+ 与（可选）`gcc`：

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# 可选：预编译 C 内核（不编译会自动即时编译，再不行则回退纯 Python）
gcc -O3 -fPIC -shared -o app/native/libalign.so app/native/align_c.c

uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
pip install pytest httpx
pytest -q
# 用纯 Python 后备内核跑同一套测试：
ALIGN_NATIVE=0 pytest -q
```

## 目录结构

```
app/
  main.py            FastAPI 应用、路由与中文错误处理
  schemas.py         请求/响应模型
  timeline.py        输入校验、对齐调度、新增字符空档均分
  quote.py           近似引语片段枚举与全局互不重叠最优选择
  revision.py        修订差异：以未改动匹配字符为边界合并有序改动块
  captions.py        字幕切分（段数最少 → 断点总分最高 → 字典序最小）
  alignment.py       内核加载（C → 即时编译 → 纯 Python）
  align_py.py        纯 Python 后备内核
  native/align_c.c   C 对齐内核源码
entrypoint.sh        容器启动：安装依赖 + 编译内核 + 启动 uvicorn
Dockerfile
docker-compose.yml
requirements.txt
tests/test_api.py
```
