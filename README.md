# 无线麦配频 API

用 FastAPI + SQLite 为最多 6 支无线麦选择唯一预存备频，并按最多 8 种干扰情形生成切换表。频点均为同一单位的整数。

## 启动

```bash
docker compose up --build
```

启动后打开：

- Swagger UI：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/health>
- 查表：<http://localhost:8000/api/table>

SQLite 数据库保存在 Compose 命名卷 `frequency-data` 的 `/data/frequency.db`。

本地直接运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

## 在 `/docs` 中的操作顺序

1. **试配/录入整套设置**：`POST /api/configuration`
2. **登记已写入麦里的备频**：`POST /api/registrations`
3. **修改干扰清单**：`PUT /api/interferences`
4. **查表**：`GET /api/table`
5. **彩排掉音复盘（新增）**：`POST /api/review`，请求体右上角 `Examples` 可直接切换
   - `single_event_explainable`：单次掉音、可解释（同时给出重频麦和互调麦对）；
   - `whole_session_no_solution`：整场对不上、返回无解；
   - `full_session_explainable`：4 个时刻、两支麦带偏移范围的整场可解释样例。

复盘请求体里的 `effective_version` 固定写的是 `1`；全新数据库第一次 `POST /api/configuration` 正好生成 v1。若你的版本号不同，把它改成 `GET /api/versions` 里的版本即可；省略 `effective_version` 时默认取当前生效版本。

仓库带有示例请求：

```bash
curl -X POST http://localhost:8000/api/configuration \
  -H 'Content-Type: application/json' \
  --data-binary @examples/configuration.json

curl -X POST http://localhost:8000/api/registrations \
  -H 'Content-Type: application/json' \
  --data-binary @examples/registration.json

curl -X PUT http://localhost:8000/api/interferences \
  -H 'Content-Type: application/json' \
  --data-binary @examples/interferences.json

curl http://localhost:8000/api/table
```

也可以运行黑盒自测脚本：

```bash
python scripts/smoke_test.py http://localhost:8000
```

## API 摘要

| 方法与路径 | 作用 |
| --- | --- |
| `POST/PUT /api/configuration` | 录入或替换名称、最多 6 支麦、每支 1～4 个候选、最多 8 种干扰，并立即生成新生效版本 |
| `GET /api/mics` | 读取当前麦和备频登记 |
| `POST/PUT /api/registrations` | 登记一支或批量登记多支麦已经写入的备频；重配时强制沿用 |
| `GET /api/interferences` | 读取当前干扰清单 |
| `PUT/POST/PATCH /api/interferences` | 整体替换干扰清单并重新配频；也接受直接提交干扰数组 |
| `GET /api/table` | 返回同一次生效版本中的设置、选定备频和切换表 |
| `POST /api/review` | 彩排掉音复盘：求能解释整场的时钟偏移，逐次列出所有可行对时下的重频麦与 `2a-b` 互调麦对；无解返回 `feasible:false` |
| `POST /api/versions/{version}/review` | 指定配频版本的复盘；另有 `/api/versions/{v}/mics/{mic}/review`、`/api/mics/{mic}/review` |
| `GET /api/configuration/current` | 当前设置与结果 |
| `GET /api/versions` | 历史版本状态 |
| `DELETE /api/registrations/{mic_id}` | 清除某支麦的强制登记 |

为便于联调，还提供 `/api/config`、`/api/mics`、`/api/backups/register`、`/api/allocation-table`、`/api/switch-table` 等别名路径。

麦可用响应中的 `mic_id` 引用；未传 ID 时按位置生成 `m1`～`m6`。登记时也支持 `mic_index`（从 0 开始）、`mic_number`（从 1 开始）或路径 `POST /api/mics/{mic_id}/installed-backup`。

## 配频与约束规则

求解器在 `app/solver.py`，不依赖 Web 框架：

1. 检查平时主频：
   - 不能重频；
   - 对三支不同麦 `i,j,k`，检查所有有序组合的 `2*f_i-f_j != f_k`。
2. 每支麦只能选一个备频：
   - 已登记备频的麦只使用登记值；
   - 其他麦按候选顺序枚举。
3. 最多 `4^6 = 4096` 个组合，计算量很小。
4. 对每种干扰：
   - 仅当麦的主频精确出现在该干扰的 `occupied_frequencies` 中时，才把它加入 `switches`；
   - 被切麦使用该麦唯一选定的备频；
   - 检查切换后的活动频点是否避开占用频点；
   - 检查切换后无重频、无 `2a-b` 落在第三支麦频点上。
5. 返回第一个按候选顺序可行的组合，结果确定。

每种输入的干扰清单条目视为一种独立“干扰情形”。如果多种干扰在同一时刻共同出现，可把它们的占用频点取并集后作为一个情形录入。

## 彩排掉音复盘：`POST /api/review`

彩排时无线麦断续掉音，这个入口回答“该查哪几支发射麦”。求解器在 `app/review.py`，不依赖 Web 框架，可直接 `review_incident(payload)` 调用（也从 `app.solver` 再导出了 `review_incident / solve_review / analyze_dropouts`）。

### 请求要素（均为整数毫秒 / 同单位整数频点）

- `effective_version`：选配频版本；省略时用当前生效版本。显式给了但不存在 → `404 version_not_found`。
- `target_mic_id`：目标麦；显式选定的版本里找不到 → `404 target_mic_not_found`。省略 `target_frequency` 时用该版本中此麦的 `main_frequency`。
- `target_frequency`：目标麦全程使用的固定频点；也可省略 ID 只给频点。
- `events`：接收机最多 **8** 个时刻，每条 `{ "time", "status" }`，`status` 取 `dropout`/`掉音` 或 `normal`/`正常`（也接受 `dropout:true/false`）。
- `mics`：其他麦的实测数据：
  - 每麦最多 **8** 段 `segments: [{ "start", "end", "frequency" }]`；
  - 区间 **含 `start`、不含 `end`**，同一支麦的区间互不重叠（触摸端点允许），重叠或 `start>=end` → `422`；
  - `clock_offset_range: [lo, hi]`：**含两端**的整数时钟偏移范围，**最多两支麦**填写，否则 `422`；也接受 `{min,max}`；
  - 偏移按需求 **加到日志时刻**：判断 `start <= time + offset < end`；
  - 不填偏移按 `0`；同一支麦整场用同一个偏移。裸整数（如 `"offset": 4`）表示已知固定偏移，不占用“两支范围麦”的名额。

字段做了常见命名兼容（`version/target/fixed_frequency/logs/records/transmitters/intervals/offset_range/…` 及中文别名），`mics/events` 也接受以 ID/时间为键的对象形式。

### 判定规则（沿用现有 `2a-b`）

在某时刻、某组偏移下，取所有正在发射（区间含 `time+offset`）的其他麦及其该段实测频点：

- **重频麦**：活动段频点恰好等于目标频点；
- **互调麦对**：两支不同的活动麦 `a,b`（有序，`a` 是公式第一项）满足现有规则 `2*f_a - f_b == target_frequency`。两种方向都会检查。

一组偏移能解释整场，当且仅当：每个 `dropout` 时刻至少命中上述一类冲突，每个 `normal` 时刻两类冲突都不命中。

### 返回内容

- 可行：`feasible:true`、`status:"feasible"`，并给 **一组** 整场偏移 `clock_offsets` / `solution`（含每麦来源 `range_solver/fixed_offset/default_zero`）。
- `events[]` 按日志顺序逐次给出：
  - `cochannel_mics`（及 ID 简表 `cochannel_mic_ids`）；
  - `intermod_pairs`（含 `a_mic_id/b_mic_id/a_frequency/b_frequency/forbidden_value`，简表 `intermod_mic_pairs` 为 `[a,b]`）；
  - 这些候选是在 **所有能解释整场的可行偏移组合上取并集**：只要存在一组合法对时让该麦/麦对在该次掉音命中，就列出，而不是只列所选那一组；
  - `at_selected_offsets` 展示返回的这组偏移在该时刻具体看到哪些活动段与冲突，便于核对；
  - `domain_possible_causes` 是整个原始偏移范围内、不考虑其他时刻约束时的“局部可能成因”，主要用于无解诊断。
- 顶层 `mics_to_check` / `suspects` 对所有掉音候选麦去重，直接给出建议排查名单及原因、出现的事件下标。
- 无解：HTTP `200` 且 `feasible:false`、`status:"infeasible"`、`solution:null`、`no_solution:{code,message,…}`（与原配频 API“约束无解也正常返回”的约定一致），并附 `domain_mics_to_check` 与每个掉音在偏移范围内的局部可能成因。

### 为什么计算量不随范围里的毫秒数增长

对事件 `t` 和段 `[s,e)`，范围麦偏移 `d∈[lo,hi]` 使该段活动的条件是 `s-t <= d < e-t`。每个段只在两个整数边界 `s-t`、`e-t` 处改变真假。把所有这些边界连同 `lo`、`hi+1` 排序切分成少量“原子格”，同一格内所有活动状态、频点和冲突判定完全相同，只需取格左端点作代表。

设 `E≤8` 时刻、每麦 `S≤8` 段，则每个偏移维度的格子数 `C≤1+2ES≤129`，范围麦 `≤2`，所以最多约 `129²≈1.7 万` 个格点组合，复杂度 `O(C1·C2·E·(MS+M²))`，与 `hi-lo` 的跨度完全无关。`solver` 字段会返回 `atomic_cells_per_dimension`、`assignments_checked` 等可核对的计量。仓库测试用小区间上的逐毫秒暴力枚举对拍了 600 个随机场景，可行性、见证偏移和候选并集逐一相符。

### 示例文件与黑盒脚本

```bash
# 先录入示例配频（全新库得到 effective_version=1）
curl -X POST http://localhost:8000/api/configuration \
  -H 'Content-Type: application/json' \
  --data-binary @examples/configuration.json

# 单次掉音可解释
curl -X POST http://localhost:8000/api/review \
  -H 'Content-Type: application/json' \
  --data-binary @examples/review_single_explainable.json

# 整场无解
curl -X POST http://localhost:8000/api/review \
  -H 'Content-Type: application/json' \
  --data-binary @examples/review_no_solution.json

# 四时刻整场可解释（两支范围麦）
curl -X POST http://localhost:8000/api/review \
  -H 'Content-Type: application/json' \
  --data-binary @examples/review_full_session.json

# 纯标准库黑盒自检（默认 :8000，可传 base_url）
python scripts/review_smoke.py http://localhost:8000
```

“单次可解释”样例中，`m2` 偏移 0 时在 t=1000 重频（480），偏移 10～20ms 时则与 `m3`(500, a)、`m2`(520, b) 构成 `2*500-520=480` 互调；两类候选都会列出，返回的见证偏移取最小可行值 `m2=0`。“整场无解”样例里 `m2∈[0,50]` 只有偏移 0 能造成 t=100 掉音的重频，但同一偏移也让 t=200 的“正常”记录命中重频，故无解。

## 持久化、并发与一致性

SQLite 中分别保存：

- `mics`：麦、主频和候选；
- `registrations`：已写入硬件、后续必须沿用的备频；
- `interferences`：干扰清单；
- `versions`：每次写入后的完整设置快照和求解结果；
- `meta.current_version`：当前生效版本。

并发登记备频和修改干扰清单时：

- 写事务使用进程内锁和 SQLite `BEGIN IMMEDIATE`；
- 事务内从规范化表读取全部当前数据，再发布一个新版本；
- 两笔更新因此串行提交，后提交的版本能看到先提交的更新，不会用旧 JSON 整体覆盖另一笔修改；
- `GET /api/table` 只读取一行 `versions` 快照，所以设置和切换表一定属于同一个 `effective_version`。

即使约束无解，也会提交新的 `versions` 记录。该版本返回：

```json
{
  "status": "infeasible",
  "feasible": false,
  "selected_backups": null,
  "switch_table": null,
  "no_solution": { "message": "..." },
  "message": "当前设置无解：..."
}
```

之后查表得到的是新版本的无解提示，不会再返回上一版可行切换表。结构性超限（例如 7 支麦、5 个候选）属于 `422`，不会保存；结构合法但频点约束无解才按“无解也保存”处理。

## 实测内容与结果

### 1. 自动化测试

可直接运行：

```bash
./scripts/run_tests.sh
```

或在已安装 `requirements-dev.txt` 的环境中执行：

```bash
pytest -q
```

本沙箱系统 Python 没有可用的 `pip/venv`，实测时把依赖装在本地 `.pydeps`（已 gitignore），因此使用的完整命令是：

```bash
PYTHONPATH=/workspace:/workspace/.pydeps python3 -m pytest -v /workspace/tests
```

结果（加入复盘功能后，2026-11-27 复跑）：

```text
37 passed, 1 warning in ~4.7s
# tests/test_api.py 7 · test_concurrency.py 2 · test_review.py 24 · test_solver.py 4
```

唯一 warning 是 TestClient 与新版 httpx/starlette 的弃用提示，与功能无关。

覆盖：

- 重频、占用频点、`2a-b=第三频点` 的检测；
- 多种干扰情形共用同一备频；
- 平时主频违反三元关系时无解；
- 备频也被干扰占用时无解；
- `/api/configuration` 与 `/api/table` 返回同一版本快照；
- 无解新设置入库，查表以无解提示取代旧切换表；
- 登记备频后，即使候选顺序改变，重配仍沿用登记值；
- 原始数组形式的干扰清单更新；
- 重启/重新创建 App 后仍读取同一 SQLite 版本；
- 最多 6 支麦、每支最多 4 候选的 `422` 校验；
- 真实 Uvicorn + HTTP 并发：12 轮“登记备频 + 修改干扰清单”同时提交，两笔更新均保留；
- 4 个并发备频登记全部保留。

复盘新增覆盖（`tests/test_review.py`，24 个用例）：

- 固定偏移下的重频命中、`2a-b` 互调麦对（含 `2*500-520=480` 数值核对）；
- `[start,end)` 起点含、终点不含的边界；偏移“加到日志时刻”的方向；
- 正常时刻禁止任何冲突；整场矛盾 → `feasible:false`；
- 候选是“所有整场可行对时的并集”：专门构造一个只在被正常时刻排除的偏移上出现的诱饵麦对，断言它不进全局候选、但保留在 `domain_possible_causes`；
- 两支范围麦必须协调到 `(3,7)` 才成立的二维网格用例；
- `±10^12ms` 超大范围只切出个位数原子格、耗时远小于阈值，代码路径不按毫秒跨度枚举；
- 标量固定偏移不占用“两支范围麦”名额；嵌套 `target` 对象；`mics_to_check/suspects` 去重名单；
- 600 个随机小场景与逐毫秒暴力枚举对拍可行性、见证偏移、候选并集（另在提交前临时跑过 3000 例更大随机对拍，0 不一致）；
- HTTP：无版本自包含调用、按版本补名/补主频、三个示例文件结果、别名与版本/麦路径、未知版本/目标 `404`、超限/重叠 `422`、`/openapi.json` 含三个 Examples、`/docs 200`。

并发用例额外重复运行 3 次，均为：

```text
2 passed
```

### 2. 真实 HTTP 黑盒冒烟

临时启动 Uvicorn 后执行：

```bash
python3 -I /workspace/scripts/smoke_test.py http://127.0.0.1:18081
```

实测输出：

```text
SMOKE TEST PASSED
base_url=http://127.0.0.1:18081
effective_version=17
shared backup: m1 -> 30 in both concurrent-a and concurrent-b
concurrent registration + interference update: both persisted
```

版本号为 17 的原因是：1 次初始配置 + 8 轮、每轮 2 个并发写事务。冒烟结束后临时 Uvicorn 进程已停止。

另外实测：

- `GET /health` → `200 {"status":"ok"}`
- `GET /docs` → `200`
- `GET /openapi.json` → `200`
- 示例配置实跑为 `feasible`，生成两条正确切换行。
- `docker-compose.yml` 已通过 YAML 解析检查，包含 API 服务、健康检查和命名卷。

### 3. 复盘入口真实 HTTP 自测（本次新增）

沙箱无 Docker，故用等价的本机 Uvicorn + 真实 TCP HTTP 验证（临时库，端口 18084；另用仅含 `requirements.txt` 的干净目标目录 `/tmp/proddeps` 起在 18085 复跑通过，确认镜像依赖足够）：

```bash
PYTHONPATH=/workspace:/workspace/.pydeps \
DATABASE_PATH=/tmp/review_final.db \
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 18084
```

两个规定记录的实测结果：

```text
single_event_explainable:
  feasible | mics_to_check=['m2','m3']
  cochannel=['m2']  intermod=[['m3','m2']]   # 2*500-520=480
  witness offsets={'m2':0,'m3':0}

whole_session_no_solution:
  infeasible feasible=False | top mics_to_check=[] | domain=['m2']

full_session_explainable:
  feasible offsets={'m2':-10,'m3':-20}
  t=0    normal  co=[]   pairs=[]
  t=1000 dropout co=['m2'] pairs=[]
  t=2000 normal  co=[]   pairs=[]
  t=3000 dropout co=[]   pairs=[['m2','m3']]
```

黑盒脚本与原配频冒烟均通过：

```text
$ python scripts/review_smoke.py http://127.0.0.1:18084
REVIEW SMOKE PASSED
$ python scripts/smoke_test.py http://127.0.0.1:18084
SMOKE TEST PASSED
```

其余实测：

- `GET /docs`、`/redoc`、`/openapi.json` 均 `200`；OpenAPI 的 `POST /api/review` 请求体含 `single_event_explainable / whole_session_no_solution / full_session_explainable` 三个 Examples，可在 Swagger 下拉切换后直接 Try it out。
- 三支麦都填 `clock_offset_range:[0,1]` → HTTP `422 {"error":"invalid_review_input", ...}`。
- 范围 `[-10^12, 10^12]` 的复盘：`assignments_checked=5`、本机 HTTP 总耗时约 `0.008s`，不随 2×10¹²+1 的毫秒跨度增长。
- `docker-compose.yml` 再次用 PyYAML 解析通过；新增 `app/review.py` 位于 `COPY app ./app` 范围内，未新增第三方依赖，现有 Dockerfile/Compose 无需改动。

## 余项

- 当前沙箱没有 `docker`/`podman` 命令、没有 `/var/run/docker.sock`、没有免密 `sudo`（且处于容器内，无法起 dockerd），因此仍未能在这里实际执行 `docker compose up --build`；已用等价的 Uvicorn 真实 HTTP（含“仅安装 `requirements.txt`”的干净依赖目录）完成全部接口、并发与复盘自测。目标机器具备 Docker Compose 时，按文首 `docker compose up --build` 启动后访问 <http://localhost:8000/docs>，用上述三个 Examples 即可试跑。
- 复盘接口设计为无状态只读分析：只从 `versions` 读取选配频版本快照用于名称/主频补全，不另写库、不产生新版本；复盘请求体里的实测区间/频点以提交值为准，不会与配频主频做强一致校验（实测频点可能与计划不同，这正是排查对象）。
- 候选互调按现有求解器的“有序 `(a,b)`”约定列出；当两支麦同频且都等于目标频点这类退化情形下，两个方向可能各产生一条记录，可用记录里的 `unordered_pair` 去重。
- 服务默认面向单人/单机调音台场景，未加鉴权和多工作区隔离；SQLite 适合该小规模配置与低频并发写。
- 原配频求解器采用确定性的候选顺序优先策略；复盘见证偏移取压缩网格上的最小可行代表值。如后续需要“最少排查麦”“偏移绝对值最小”等偏好，可在已得到的可行格集合上再加排序指标。
