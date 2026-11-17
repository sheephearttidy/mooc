# 英华学堂网课助手（Python 版）

英华学堂网课助手客户端 —— 用 Python 完全重构的版本。

作者: Pluto  
禁止二次售卖, 仅供学习与交流  
代刷慕课的请不要使用, 我讨厌代刷慕课的

> 本仓库原为 Go 实现（`v1.3.2`），现已整体重写为 Python（`v2.0.0`）。
> 老的 Go 代码可在 git 历史中找回，例如 `git show e6e7a13:main.go`。

## 演示

![](docs/preview_1.gif)

### web端

![](docs/preview_2.png)

## 特性

* 只刷**视频**节点，试题 / 作业 / 考试一律跳过
* 已完成的课程、已关闭的课程自动跳过
* 多账号、多课程并发，并发数由 `limit` 控制
* 同一个账号只登录一次，多门课复用会话（避免触发风控）
* 内置 Web 面板实时查看日志，默认只监听 `127.0.0.1`
* 心跳超时、验证码、进度轮询失败都有重试上限，不会无限卡死
* 支持 `Ctrl+C` 优雅退出，日志滚动切割（5 MB × 3 份）

## 安装

需要 Python 3.9 或更高版本。

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## 配置

把 `sample.config.json` 复制成 `config.json` 再改：

```json
{
  "global": {
    "server": "127.0.0.1:10086",
    "limit": 3
  },
  "users": [
    {
      "base_url": "https://mooc.yinghuaonline.com",
      "school_id": 0,
      "username": "username",
      "password": "password"
    }
  ]
}
```

* `base_url` 填自己学校的平台域名，**不要带路径**
* `school_id` 一般填 `0`
* `server` 是 Web 面板监听地址，只写 `:10086` 等价于 `127.0.0.1:10086`
* `limit` 是并发刷课数，填大一点更快（会自动收敛到实际课程数，`<= 0` 时按 `3` 处理）
* 多账号就往 `users` 数组里继续加对象
* 不想要 Web 面板就启动时加 `--no-web`（`server` 不填时默认 `127.0.0.1:10086`）

## 运行

```bash
python main.py
```

或者用模块方式（两者等价）：

```bash
python -m moocpy
```

或者装成命令（`pip install -e .` 之后）：

```bash
moocpy
```

启动后打开 <http://127.0.0.1:10086> 就能看到实时日志。

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `-c, --config PATH` | 配置文件路径，默认 `config.json` |
| `--no-web` | 不启动 Web 面板 |
| `--no-copyright` | 不打印启动横幅 |
| `--dry-run` | 只登录并列出待刷课程，不实际刷课 |
| `--interval SEC` | 心跳与进度轮询间隔，默认 `10` |
| `--max-restarts N` | 单个节点进度轮询失败后的最大重开次数，默认 `3` |
| `--log-dir DIR` | 日志目录，默认 `logs` |
| `--version` | 打印版本号 |

先干跑一遍确认账号和课程都识别正常：

```bash
python -m moocpy --dry-run
```

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest
```

测试不访问真实服务器：`tests/fakes.py` 提供了可编程的假会话，`tests/test_bootstrap.py` 里还起了一个本地 HTTP 服务模拟全套接口做端到端验证。

## 目录结构

```
main.py           项目根入口，等价于 python -m moocpy
moocpy/
  __main__.py     python -m moocpy 入口
  bootstrap.py    CLI 参数、启动流程、信号处理
  config.py       config.json 解析与校验
  errors.py       异常层次
  http.py         requests 会话（超时、重试）
  logger.py       控制台 + 滚动文件 + 内存环形缓冲
  models.py       响应数据模型
  task.py         任务收集与线程池调度
  web.py          Web 日志面板
  yinghua.py      英华学堂接口客户端与刷课状态机
  captcha.py      验证码识别
view/index.html   Web 面板页面
tests/            单元测试与端到端测试
```

## 从 Go 版迁移

* 命令行：`aoaostar_mooc.exe` → `python main.py`（或 `python -m moocpy`）
* 配置文件 `config.json` 格式**完全兼容**，无需改动
* `scripts/` 下的三个 `.bat` 已改成调用 `.venv` 里的 Python，双击仍可用
* 日志文件名仍是 `logs/aoaostar.log`，Web 面板地址仍是 `127.0.0.1:10086`
* 服务端接口、登录参数、心跳节奏与原版一致；改动的是并发调度、超时重试与错误处理

## 免责声明

本项目仅供学习与交流使用，请勿用于商业用途。使用本工具产生的任何后果由使用者自行承担。
