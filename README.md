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

结果：

```text
13 passed
```

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

## 余项

- 当前沙箱没有 `docker`/`podman` 命令，也没有免密 `sudo` 可安装 Docker，因此未能在这里实际执行 `docker compose up --build`；已用等价的本地 Uvicorn 真实 HTTP 服务完成接口和并发自测。目标机器具备 Docker Compose 时可直接按上面的命令启动。
- 服务默认面向单人/单机调音台场景，未加鉴权和多工作区隔离；SQLite 适合该小规模配置与低频并发写。
- 求解器采用确定性的候选顺序优先策略；如后续需要“最少切换”“最大频点间隔”等偏好，可在枚举到的可行组合上增加排序指标。
