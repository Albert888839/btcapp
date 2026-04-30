[app]
title = BTC Scanner
package.name = btcscanner
package.domain = org.btctools
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
requirements = python3,kivy,ecdsa,base58,requests
android.permissions = INTERNET,WAKE_LOCK
android.archs = arm64-v8a, armeabi-v7a
log_level = 2
warn_on_root = 1
