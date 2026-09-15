# 无前台窗口启动

Windows默认入口使用powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden，
由scripts/launch-hidden.ps1以CreateNoWindow启动uv。MCP标准输入、输出、错误使用独立字节流转发，不经PowerShell文本格式化。
父端关闭输入后向子端传递EOF，正常退出返回子进程退出码。
内部deadline worker也使用CREATE_NO_WINDOW和SW_HIDE，不使用会使该标志失效的DETACHED_PROCESS组合。

不打开终端标签页，不自动打开浏览器；日志通过MCP的stderr交给宿主。
不会隐藏用户自己的终端，不关闭已有服务窗口。已运行的旧进程需要重新连接才能使用新入口。
宿主创建最外层进程时仍应使用无窗口方式；不同宿主需自行验证是否有瞬时窗口。

默认配置针对Windows。Linux/macOS使用mcp.posix.json中的uv启动定义；
无需PowerShell，不改变独立插件的数据目录与认证方式。

验证：scripts/check_setup_mcp.py读取实际mcp.json，用空的临时配置目录测试
initialize、list_tools和setup_status，不载入真实配置，不启动投放worker。
