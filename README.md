# PCL2 下载功能恢复补丁

给 PCL2 2.99+ 版本用的，把被龙猫关掉的 Minecraft 本体下载功能恢复回来。

## 龙猫是怎么禁用下载的

2.99.0.0 版本开始，在 `ModSecret.vb` 里加了一行：

```vb
Public Const RestrictMcDownload As Boolean = True
```

就这一行。但因为是 Const，VB.NET 编译器编译的时候直接把 True 内联到所有用到它的地方，然后做常量折叠，结果就是四处代码被改了：

1. **下载页版本列表加载器** `LoaderInit`：本来应该加载 `DlClientListLoader` 去拉版本列表，被替换成一个直接抛"关闭下载"异常的假 LoaderTask，点下载就报错。
2. **整合包安装** `ModpackInstall(File)`：所有正常执行路径最后都被改跳到 throw 限制异常的地方，装不了整合包。
3. **整合包安装（无参）** `ModpackInstall()`：整个方法被折叠成弹个 Hint 提示然后 Return，啥也不干。
4. **下载页 Minecraft 卡片** `LoadInstanceFinished`：CardMinecraft 可见性被硬编码成 Collapsed，直接藏起来。

一个常量开关，编译器帮他把所有下载相关的代码路径全堵死了。

## 补丁原理

不重新编译，直接对编译好的 exe 做 .NET IL 二进制原位修改：

1. `LoaderInit`：假 LoaderTask 构造代码（48字节）替换为 `ldsfld DlClientListLoader`，恢复真实版本列表加载
2. `ModpackInstall(File)`：末尾限制 throw（11字节）改为 `ldnull; ret`
3. `ModpackInstall()`：整个方法替换为 `ret`（空方法）
4. `LoadInstanceFinished`：CardMinecraft 可见性 Collapsed 改为 Visible

文件大小不变，自动备份原文件为 .bak。也支持 VB 源码模式，直接把 ModSecret.vb 里的 True 改成 False。

## 持久化

打补丁后自动安装守护程序，复制到 PCL 目录并注册开机启动。每次开机自动全盘扫描，发现 PCL 更新导致补丁被覆盖就自动重新打，下载功能不会因为更新又没了。

## 使用

1. 运行 PCL2_Patcher.exe
2. 点"选择磁盘自动搜索"，勾上 PCL 所在的盘
3. 点"打补丁并安装守护"
4. 完事

## 文件

- PCL_Patcher.py — 源码
- PCL2_Patcher.exe — 打包好的可执行文件
- 
