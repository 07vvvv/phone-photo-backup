# -*- coding: utf-8 -*-
"""dev/make_share_zip.py — 打一个能直接发给别人的分享包

包里只有两样：PhonePhotoBackup.exe + 使用说明.txt。

**绝不能带 config.json**：里面有你的备份目录和设备名映射（android_id、手机型号）。
error.log / .adb / .pulltmp / .adbtmp / .thumbcache 同理，一个都不进包。
脚本最后会自己校验一遍包内条目，多出来的东西直接判失败。
"""
import hashlib
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "dist", "PhonePhotoBackup.exe")
OUT = os.path.join(ROOT, "dist", "手机照片备份-分享包.zip")
INNER = "PhonePhotoBackup"          # 包内目录名用 ASCII，解压到哪都不会乱码

FORBIDDEN = ("config.json", "error.log", "motion-check.log", ".adb", ".adbtmp",
             ".pulltmp", ".thumbcache", "_MEI", "config.json.tmp")

README_TXT = """手机照片备份 —— 使用说明
================================================

这是什么
  把安卓手机里的照片和视频备份到电脑的小工具，备份完可以按时间浏览。
  只读手机：不会删除、移动、修改手机上的任何东西。

一、先把它放好
  1. 把整个文件夹解压到「桌面」或「文档」这类可写的位置。
     不要放进 C:\\Program Files —— 程序启动时要往自己所在目录写临时文件，
     只读目录会导致启动失败。
  2. 双击 PhonePhotoBackup.exe。
  3. 第一次打开会让你选一个文件夹用来存放照片，挑一个空间够大的位置
     （比如 D:\\PhonePhotos）。点「取消」也行，程序会落到「图片\\PhonePhotos」。
     以后想换：左上「更多 → 更改备份目录」。

  Windows 可能弹出蓝色的「Windows 已保护你的电脑」：
  点「更多信息」→「仍要运行」。这是因为程序没买代码签名证书，不是病毒。

二、手机上要做的
  1. 用数据线连电脑（要用能传数据的线，不能是只充电的线）。
  2. 手机弹出「允许 USB 调试吗」时点「允许」。
  3. 没弹授权框的话，先打开「开发者选项」：
     设置 → 关于手机 → 连点「版本号」7 次，
     然后在 设置 → 系统 → 开发者选项 里打开「USB 调试」。
     小米 / 红米：设置 → 我的设备 → 全部参数与信息 → 连点「OS 版本」7 次。
  4. 回到程序，顶栏「连接」里出现你的手机后，点右上角「开始备份」。

三、无线连接（可选，Android 11 及以上）
  手机：设置 → 开发者选项 → 无线调试 → 打开。
  程序：点顶栏「无线」→「重新发现」→ 按提示填 6 位配对码 → 「配对并连接」。
  手机和电脑要在同一个 Wi-Fi 下。

四、备份完在哪
  按这样的结构存放（每台手机一个目录，目录名取自手机型号）：
    <备份目录>\\<手机名>\\Camera\\2026\\2026-10-04\\IMG_20261004_120000.jpg
                                \\Screenshots\\...   截图
                                \\ScreenRecorder\\... 录屏
                                \\Other\\...         微信、QQ 等保存的图和视频
  想在程序里看到磁盘上新增的文件：点顶栏「刷新」。

五、安全说明（重要）
  · 程序只读手机，代码里没有任何删除、移动、上传手机文件的路径。
  · 浏览时右键「移到回收站」删的是电脑上的副本，进的是 Windows 回收站，
    可以还原；手机上的原图不受影响。
  · 重复备份是安全的：已经备份过的会自动跳过，不会重复占空间。
  · 备份中途拔线也没关系：重新连上再点「开始备份」，会从断点接着来。

六、常见问题
  · 「未检测到手机」：换根数据线 / 换个 USB 口；手机通知栏把 USB 用途改成「文件传输」；
    确认「USB 调试」是开着的；弹出授权框要点「允许」。
  · 有些格子显示「▶ 视频」或空白：系统缺对应的缩略图解码器
    （HEIC 需要装 HEIF 扩展），文件本身照常备份，双击照样能看能播。
  · 备份很慢：第一次全量本来就慢（约每秒 20 MB），之后就只拉新增的了。

程序目录里会出现的这几个文件都是它自己产生的，可以随时删：
  config.json（记住你的备份目录和设备名）、error.log（出错日志）、.adb\\（内置 adb）。
"""


def main():
    if not os.path.isfile(EXE):
        print(f"没有 {EXE}\n先构建：pyinstaller --noconfirm PhonePhotoBackup.spec")
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(EXE, f"{INNER}/PhonePhotoBackup.exe")
        z.writestr(f"{INNER}/使用说明.txt", README_TXT)

    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
        print("包内文件：")
        for n in names:
            print(f"  {n}   {z.getinfo(n).file_size:,} 字节")
        bad = [n for n in names if any(f in n for f in FORBIDDEN)]
        if bad or len(names) != 2:
            print(f"\n✗ 包里有不该带的东西：{bad or names}")
            return 1
    with open(OUT, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    print(f"\n✓ 干净：只有 exe + 使用说明，没有任何本机使用数据")
    print(f"  {OUT}")
    print(f"  {os.path.getsize(OUT):,} 字节   SHA256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
