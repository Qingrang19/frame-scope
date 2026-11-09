<p align="center"><img src="assets/logo.svg" width="520" alt="frame-scope 标志"></p>

# frame-scope

**先检查日志，再讨论波形。** 一个完全离线、仅依赖 Python 标准库的测量日志检查与重放 CLI，用于 EIT（电阻抗成像）或嵌入式实验的记录质量排查。它不读取串口、不控制硬件，不做 EIT 重建，也不提供医疗判断。

## 30 秒运行

需要 Python 3.10+。在本仓库根目录运行，无需安装、网络或 pyserial：

```sh
python -m frame_scope demo --frames 100 --channels 4 > demo.ndjson
python -m frame_scope check demo.ndjson
python -m frame_scope check demo.ndjson --format csv > report.csv
python -m frame_scope replay demo.ndjson > replay.ndjson
python -m frame_scope demo --frames 10 | python -m frame_scope check -
python -m unittest discover -s tests -v
```

`check` 默认输出 JSON；CSV 使用 `section,metric,channel,value` 长表，兼顾总体诊断和逐通道统计。标准输出只包含数据，逐行错误发到标准错误，可分别保存：

```sh
python -m frame_scope demo --frames 5 --errors > broken.ndjson
python -m frame_scope check broken.ndjson > report.json 2> diagnostics.txt
# 此命令发现异常，退出码应为 1，而非执行失败。
python -m frame_scope replay broken.ndjson > accepted.ndjson 2> replay-errors.txt
```

也可在已有 setuptools 的开发环境执行 `python -m pip install --no-build-isolation -e .`，之后使用 `frame-scope check demo.ndjson`。运行时依赖为零；安装构建由 setuptools 提供，完全离线时优先使用上面的 `python -m` 命令。

## 自定义日志协议 v1

每个 UTF-8 文本行恰好一个 JSON 对象，字段必须且只能如下：

```json
{"seq":0,"timestamp":0.01,"channels":[0.13,-0.27,0.42]}
```

- `seq`：非负整数，不能是布尔值。第一帧可从任意序号开始，没有隐式缺失前缀。没有自动回绕或重启识别。
- `timestamp`：有限非负数字，单位为秒，建议使用相对实验开始的时间。不强制等间隔；不接受日期字符串。
- `channels`：非空有限数字数组，不能包含布尔值、字符串、NaN 或 Infinity。整数也必须可表示为有限 binary64 数值。
- 首个有效帧锁定通道数；之后改变通道数的行被拒绝。拒绝行不更新序号、时间或统计状态。
- 多余字段、重复 JSON 键、损坏 JSON 都拒绝；空白行单独计数并跳过。诊断行号从 1 开始。
- 默认单行上限 1,048,576 字符（包括换行），可用 `--max-line-chars 4096` 调整。超长行整行丢弃，后续行继续处理。UTF-8 解码失败属于文件错误，终止当前命令。

### 完整性计数的精确定义

所有判断只针对有效帧，按输入顺序进行：

| 指标 | 语义 |
| --- | --- |
| duplicates | 序号此前出现过，每次再次出现加 1 |
| out_of_order | 序号首次出现且低于此前最大序号，加 1；与重复计数互斥 |
| forward_gap_events | 新序号高于此前最大序号加 1，每次跳跃加 1 |
| forward_gap_slots | 每次前向跳跃跨过的槽位数之和，后续补到不扣减 |
| timestamp_regressions | 时间戳低于上一有效帧的时间戳；相等不算异常 |

例如序列 `5,8,8,6,6,9`：重复 2、乱序 1、前向跳跃 1、跳过槽位 2。这里的槽位不是“最终丢包数”，也不是损坏行数。所有有效帧，包括重复和乱序帧，都参与通道统计，不做静默去重。

## 统计与重放

每个通道输出样本数、最小值、最大值、均值和**总体标准差**（除以 N）。通道编号从 0 开始。内部以 40 位精度 Decimal 的 Welford 递推计算，避免有限大幅值输入的平方导致浮点溢出。JSON 中统计数值为十进制**字符串**，样本数仍为整数；计算结果是近似值，不承诺任意精度。空文件成功并返回零计数、空通道列表。

`replay` 将有效帧按到达顺序输出成规范化 NDJSON，保留重复/乱序记录，跳过无效行。不等待时间戳、不倍速播放、不绘图、不发到硬件。输出可以继续管道传给其他离线分析程序。

退出码：`0` 未发现异常（空白行不算异常），`1` 发现无效帧、重复、乱序、前向跳跃或时间倒退，`2` 参数、文件或编码错误。错误退出之前可能已经写出部分重放结果，请检查退出码再使用产物。

## 架构

```text
UTF-8 file / stdin
  -> bounded line reader -> schema validation -> Analyzer
                                             -> RunningStats
                                             -> JSON / CSV report
                                             -> accepted NDJSON replay
```

- `frame_scope/core.py`：无 IO 副作用的单帧校验、流读取、完整性计数和统计。
- `frame_scope/__main__.py`：参数、文件与标准流、输出格式、确定性演示生成器。
- `tests/test_frames.py`：协议、恢复、数值边界、统计、计数与子进程 CLI 冒烟测试。
- `assets/logo.svg`：本项目原创的括号/采样点矢量标志。
- `examples/clean.ndjson`、`examples/mixed.ndjson`：由本项目生成器生成的可复现样例。

## 测试与边界

```sh
python -m unittest discover -s tests -v
python -m compileall -q frame_scope tests
python -m frame_scope check examples/clean.ndjson
python -m frame_scope check examples/mixed.ndjson --format csv
# 最后一条预期退出 1：有意混入错误。
```

流式处理不保存全部帧；但为了精确判断非相邻重复，保存已见序号集合，内存随唯一序号数增长（O(U+C)）。不适合作为无限运行或不可信网络输入的防护服务。单行限制不是进程总内存限制，通道数受单行限制约束。没有 CRC、二进制解包、单位换算、物理校准、时间同步、真实设备验证或临床安全认证。实验操作前仍需独立验证采集链路。

## 独立性与授权

本项目为独立实现；未读取或复制其他项目的源码、文档或图形资产。协议、CLI、测试、中文说明和标志均为本项目编写。仅面向离线实验数据检查，分发采用 MIT 许可证，见 `LICENSE`。
