# 通用 release

固定启动文件：`build\release\AllianceBossStrategyStudio.exe`。

在 Windows 中为这个文件创建桌面快捷方式。不要把 EXE 本身复制到桌面，也不要让快捷方式指向带版本号的旧文件；快捷方式必须继续指向上述固定路径。当前程序版本为 1.0.5，以后升级版本时该路径也不变。策略和日志继续使用原用户数据目录。

## 发布流程

`tools/build_chimera_desktop.ps1` 默认先将单文件 EXE 构建到 `out/chimera-desktop/packages/<构建时间>/`，成功后调用 `tools/publish_chimera_release.py` 更新固定入口。

发布会校验新包、备份之前的固定入口，再原子替换并核对结果。版本、发布时间和 SHA-256 写入 `build/release/latest.json`，校验值同时写入 `AllianceBossStrategyStudio.sha256`。旧入口保存在 `build/release/archive`，不自动删除。已有的带版本号 EXE 作为历史文件保留，不是后续启动入口。

如果需要先核对包内容，可使用 `-SkipPublish` 构建，验证后调用发布脚本。`-OutputDirectory` 只控制构建包的输出位置，不改变固定 release 的目标路径；`-OneDir` 需要整个程序目录，不能发布到本单文件入口。

```powershell
# 已完成前端和原生代理构建后，生成并发布单文件桌面程序。
& .\tools\build_chimera_desktop.ps1

# 先生成包，验证通过后再发布。
& .\tools\build_chimera_desktop.ps1 -SkipPublish
```

如目标文件被占用，发布报错，旧入口和新构建包都保留。不要终止用户进程，也不要改用另一个启动路径来绕过占用。告知用户关闭工具，随后对同一个已验证新包重试发布，无需重新构建：

```powershell
# 以下变量应填入此次已验证包的实际路径、版本和校验值。
python .\tools\publish_chimera_release.py --source $packagePath --version $packageVersion --expected-sha256 $verifiedSha256
```

发布不会启动程序、操作游戏或改动策略。更新后，下次打开桌面快捷方式即运行最近一次成功发布的版本；已经打开的进程需要退出后重新打开。

## 验证

`python tools/test_release_publish.py` 使用隔离目录检验同版本更新、跨版本固定路径、旧包备份、重复发布、校验失败及 Windows 文件占用后关闭重试。占用验证使用真实文件共享锁，不启动工具或游戏。该测试也包含在 `tools/run_offline_tests.py` 的离线测试入口中。
