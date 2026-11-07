# -*- coding: utf-8 -*-
import sys
import os
import json
import uuid
import datetime
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

import webview
import sympy as sp
from groq import Groq

try:
    from duckduckgo_search import DDGS
    HAS_WEB_SEARCH = True
except ImportError:
    HAS_WEB_SEARCH = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")
CONFIG_FILE = os.path.join(BASE_DIR, "app_config.json")


class BackendAPI:
    """PythonとWebView(HTML/JS)を繋ぐ双方向APIブリッジ"""
    def __init__(self):
        self.api_key = os.environ.get("GROQ_API_KEY", "").strip()
        self.selected_model = "openai/gpt-oss-120b"
        self.enable_web_search = HAS_WEB_SEARCH
        self.client = None
        self.current_session_id = None
        self.sessions = []

        self.setup_sympy_env()
        self.init_groq()
        self.load_history_from_disk()

    def setup_sympy_env(self):
        x, y, z, t = sp.symbols('x y z t')
        self.env = {
            'x': x, 'y': y, 'z': z, 't': t,
            'pi': sp.pi, 'E': sp.E, 'I': sp.I,
            'sin': sp.sin, 'cos': sp.cos, 'tan': sp.tan,
            'sqrt': sp.sqrt, 'log': sp.log, 'exp': sp.exp,
            'expand': sp.expand, 'factor': sp.factor,
            'solve': sp.solve, 'diff': sp.diff, 'integrate': sp.integrate
        }

    def init_groq(self):
        if self.api_key:
            try:
                self.client = Groq(api_key=self.api_key)
            except Exception:
                self.client = None

    def get_system_prompt(self):
        now_str = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M")
        return (
            "あなたは日本語に特化した親切・論理的な万能AIアシスタント「LLMCalcJP」です。\n"
            f"現在の日本時間は【 {now_str} 】です。日本の行政区分・地理を正確に理解して答えてください。\n"
            "Kotlin（Minecraft Mod開発など）、Java、Python、C++等のプログラミング質問にも深く精通しています。\n\n"
            "【ツールの使い分けの指示】\n"
            "1. calculate_formula: 因数分解、方程式の解、微分積分などの厳密な数学計算を行う場合のみ呼び出します。\n"
            "2. search_web: 天気予報や最新ニュースを調べる場合に呼び出します。\n\n"
            "コードを提示する際は言語名（例: ```kotlin, ```java）を明記し、美しく実用的なマークダウンで答えてください。"
        )

    # --- 履歴 ＆ 設定管理 ---

    def load_history_from_disk(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.sessions = data.get("sessions", [])
                    self.current_session_id = data.get("current_session_id")
            except Exception:
                self.sessions = []
                self.current_session_id = None

        if not self.sessions:
            self.create_new_session_internal()
        elif not self.get_current_session():
            self.current_session_id = self.sessions[0]["id"]

    def save_history_to_disk(self):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "current_session_id": self.current_session_id,
                    "sessions": self.sessions
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def create_new_session_internal(self):
        new_id = str(uuid.uuid4())[:8]
        new_session = {
            "id": new_id,
            "title": "新しいチャット",
            "created_at": datetime.datetime.now().strftime("%m/%d %H:%M"),
            "messages": [{"role": "system", "content": self.get_system_prompt()}]
        }
        self.sessions.insert(0, new_session)
        self.current_session_id = new_id
        self.save_history_to_disk()
        return new_session

    def get_current_session(self):
        for s in self.sessions:
            if s["id"] == self.current_session_id:
                return s
        return None

    # --- JS連携API ---

    def get_sessions_list(self):
        return {
            "current_id": self.current_session_id,
            "sessions": [{"id": s["id"], "title": s["title"], "time": s["created_at"]} for s in self.sessions]
        }

    def switch_session(self, session_id):
        self.current_session_id = session_id
        self.save_history_to_disk()
        session = self.get_current_session()
        msgs = [m for m in session.get("messages", []) if m.get("role") in ["user", "assistant"]]
        return {"status": "success", "session": session, "messages": msgs}

    def create_new_session(self):
        new_s = self.create_new_session_internal()
        return {"status": "success", "session_id": new_s["id"]}

    def delete_session(self, session_id):
        self.sessions = [s for s in self.sessions if s["id"] != session_id]
        if not self.sessions:
            self.create_new_session_internal()
        elif self.current_session_id == session_id:
            self.current_session_id = self.sessions[0]["id"]
        self.save_history_to_disk()
        return self.get_sessions_list()

    def calculate_sympy(self, expr_str):
        try:
            parsed = sp.sympify(expr_str, locals=self.env)
            if parsed.is_number:
                eval_num = parsed.evalf()
                res = str(int(eval_num)) if eval_num.is_Integer else str(eval_num)
            else:
                res = str(parsed)
            return {"status": "success", "result": res}
        except Exception as e:
            return {"status": "error", "result": f"計算エラー: {e}"}

    def run_web_search(self, query):
        if not HAS_WEB_SEARCH or not self.enable_web_search:
            return "Web検索が無効化されています。"
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, region="jp-jp", max_results=4))
            if not results:
                return "検索結果が見つかりませんでした。"
            snippets = [f"【{r.get('title')}】\n{r.get('body')}" for r in results if r.get('body')]
            return "\n\n".join(snippets)
        except Exception as e:
            return f"検索エラー: {e}"

    def get_settings(self):
        theme_cfg = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    theme_cfg = json.load(f)
            except Exception:
                pass
        return {
            "api_key": self.api_key,
            "model": self.selected_model,
            "web_search": self.enable_web_search,
            "wallpaper": theme_cfg.get("wallpaper", ""),
            "overlay_opacity": theme_cfg.get("overlay_opacity", 0.75),
            "blur_val": theme_cfg.get("blur_val", 8)
        }

    def update_settings(self, new_key, new_model, new_web_search, wallpaper, overlay_opacity, blur_val):
        self.api_key = new_key.strip()
        self.selected_model = new_model.strip()
        self.enable_web_search = bool(new_web_search)
        self.init_groq()

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "wallpaper": wallpaper,
                    "overlay_opacity": float(overlay_opacity),
                    "blur_val": int(blur_val)
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return {"status": "success"}

    def regenerate_response(self):
        session = self.get_current_session()
        if not session or not session.get("messages"):
            return {"status": "error", "message": "再生成できる会話がありません。"}

        while session["messages"] and session["messages"][-1].get("role") in ["assistant", "tool"]:
            session["messages"].pop()

        if session["messages"] and session["messages"][-1].get("role") == "user":
            return self.communicate()

        return {"status": "error", "message": "再生成できる質問が見つかりませんでした。"}

    def send_message(self, user_text, file_name="", file_content=""):
        if not self.client:
            return {"status": "error", "message": "Groq API Key が未設定です。「環境設定」からキーを入力してください。"}

        session = self.get_current_session()
        if not session:
            session = self.create_new_session_internal()

        if session["title"] == "新しいチャット":
            title_text = user_text.strip().replace("\n", " ")[:18]
            session["title"] = title_text or (file_name[:18] if file_name else "チャット")

        full_content = user_text
        if file_content:
            full_content = f"【添付ファイル: {file_name}】\n```\n{file_content}\n```\n\n{user_text}"

        session["messages"].append({"role": "user", "content": full_content})
        self.save_history_to_disk()

        return self.communicate()

    def send_code_prompt(self, instruction, current_code, language):
        """AIコーディング専用API：コードと指示を受け取り改善・生成"""
        if not self.client:
            return {"status": "error", "message": "Groq API Key が未設定です。"}

        prompt = (
            f"あなたはプロのソフトウェアエンジニアです。言語: 【{language}】\n"
            f"ユーザーからの指示: {instruction}\n\n"
            f"現在のコード:\n```{language}\n{current_code}\n```\n\n"
            f"上記の指示に従い、完成した修正版コードと簡単な解説を日本語で提示してください。\n"
            f"コードブロックには必ず言語識別子 ```{language} を付けてください。"
        )

        try:
            completion = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.selected_model,
                temperature=0.2,
                max_tokens=1500
            )
            reply = completion.choices[0].message.content or ""
            return {"status": "success", "reply": reply}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def communicate(self):
        session = self.get_current_session()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "calculate_formula",
                    "description": "数式の因数分解、方程式の求解、微分積分を行う場合のみ呼び出します。",
                    "parameters": {
                        "type": "object",
                        "properties": {"formula": {"type": "string", "description": "SymPy形式の数式文字列"}},
                        "required": ["formula"]
                    }
                }
            }
        ]
        if HAS_WEB_SEARCH and self.enable_web_search:
            tools.append({
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "天気予報や最新ニュースを検索します。",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "検索キーワード"}},
                        "required": ["query"]
                    }
                }
            })

        session["messages"][0]["content"] = self.get_system_prompt()

        try:
            completion = self.client.chat.completions.create(
                messages=session["messages"],
                model=self.selected_model,
                tools=tools,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=800
            )

            choice = completion.choices[0]
            resp_msg = choice.message
            tool_calls = getattr(resp_msg, "tool_calls", None)

            if not tool_calls:
                reply_text = resp_msg.content or ""
                session["messages"].append({"role": "assistant", "content": reply_text})
                self.save_history_to_disk()
                return {"status": "success", "reply": reply_text, "session_title": session["title"]}

            raw_tool_calls = [{
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
            } for tc in tool_calls]

            session["messages"].append({
                "role": "assistant",
                "content": resp_msg.content or "",
                "tool_calls": raw_tool_calls
            })

            for tc in tool_calls:
                fn_name = tc.function.name
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}

                if fn_name == "calculate_formula":
                    f_str = args.get("formula", "")
                    c_res = self.calculate_sympy(f_str)
                    tool_res = str(c_res.get("result", ""))
                elif fn_name == "search_web":
                    q_str = args.get("query", "")
                    tool_res = self.run_web_search(q_str)
                else:
                    tool_res = "未知のツールです。"

                session["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": tool_res
                })

            final_completion = self.client.chat.completions.create(
                messages=session["messages"],
                model=self.selected_model,
                temperature=0.3,
                max_tokens=800
            )

            reply_text = final_completion.choices[0].message.content or ""
            session["messages"].append({"role": "assistant", "content": reply_text})
            self.save_history_to_disk()
            return {"status": "success", "reply": reply_text, "session_title": session["title"]}

        except Exception as e:
            return {"status": "error", "message": str(e)}


HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="ja" class="dark">
<head>
  <meta charset="UTF-8">
  <title>LLMCalcJP</title>
  <!-- Tailwind CSS -->
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            geminiBg: '#131314',
            geminiCard: '#1e1f20',
            geminiSidebar: '#171719',
            geminiBorder: '#333537',
            geminiBlue: '#1a73e8'
          }
        }
      }
    }
  </script>
  <!-- Marked, Highlight, KaTeX -->
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
  <!-- Kotlin, Java, Gradle等のハイライトサポート -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/kotlin.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/gradle.min.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css">
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"></script>

  <style>
    /* 壁紙レイヤーとすりガラス (Glassmorphism) 効果 */
    #bg-layer {
      position: fixed; inset: 0; background-size: cover; background-position: center; background-repeat: no-repeat;
      z-index: 0; pointer-events: none; transition: background-image 0.3s ease;
    }
    #glass-overlay {
      position: fixed; inset: 0; z-index: 1; pointer-events: none; transition: background-color 0.2s, backdrop-filter 0.2s;
    }
    .glass-card {
      background-color: rgba(30, 31, 32, var(--glass-card-alpha, 0.82)) !important;
      backdrop-filter: blur(var(--glass-blur, 8px));
    }
    .glass-sidebar {
      background-color: rgba(23, 23, 25, var(--glass-card-alpha, 0.88)) !important;
      backdrop-filter: blur(var(--glass-blur, 8px));
    }

    .prose table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
    .prose th, .prose td { border: 1px solid #3e4042; padding: 8px 14px; text-align: left; }
    .prose th { background-color: rgba(42, 43, 46, 0.9); }
    .prose pre { position: relative; background: #18191c; border-radius: 8px; padding: 12px; overflow-x: auto; margin: 12px 0; }
    .copy-btn { position: absolute; top: 8px; right: 8px; background: #2f3034; color: #b0b3b8; border: none; padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; }
    .copy-btn:hover { background: #3e4044; color: #fff; }
  </style>
</head>
<body class="bg-geminiBg text-gray-100 flex flex-col h-screen overflow-hidden select-text font-sans relative">

  <!-- 壁紙 ＆ 透過オーバーレイ -->
  <div id="bg-layer"></div>
  <div id="glass-overlay"></div>

  <!-- アプリ本体（最前面 z-10） -->
  <div class="relative z-10 flex flex-col h-screen overflow-hidden">

    <!-- 上部バー -->
    <div class="flex items-center justify-between border-b border-geminiBorder px-4 py-2.5 glass-card select-none">
      <div class="flex items-center space-x-3">
        <button onclick="toggleSidebar()" class="p-1.5 hover:bg-gray-700 rounded-lg text-gray-300" title="サイドバー開閉">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path></svg>
        </button>
        <div class="flex space-x-1.5">
          <button onclick="switchTab('chat')" id="tab-chat" class="px-4 py-1.5 text-xs font-semibold rounded-lg bg-geminiBlue text-white shadow">✨ AI アシスタント</button>
          <button onclick="switchTab('code')" id="tab-code" class="px-4 py-1.5 text-xs font-semibold rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700">💻 AI コーディング</button>
          <button onclick="switchTab('calc')" id="tab-calc" class="px-4 py-1.5 text-xs font-semibold rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700">📐 数式計算機</button>
          <button onclick="switchTab('settings')" id="tab-settings" class="px-4 py-1.5 text-xs font-semibold rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700">⚙️ 環境設定</button>
        </div>
      </div>
      <div class="flex items-center space-x-3">
        <span class="text-xs px-2.5 py-1 rounded bg-blue-950/80 text-blue-300 border border-blue-800">⚡ SymPy連携: ON</span>
        <button onclick="toggleTheme()" id="theme-btn" class="text-xs bg-gray-800/80 hover:bg-gray-700 text-yellow-400 px-3 py-1.5 rounded-lg border border-geminiBorder">☀️ ライト</button>
      </div>
    </div>

    <!-- メイン領域 -->
    <div class="flex flex-1 overflow-hidden relative">

      <!-- 左サイドバー -->
      <div id="sidebar" class="w-64 glass-sidebar border-r border-geminiBorder flex flex-col select-none transition-all duration-200">
        <div class="p-3 border-b border-geminiBorder">
          <button onclick="startNewChat()" class="w-full flex items-center justify-center space-x-2 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold shadow">
            <span>➕ 新しいチャット</span>
          </button>
        </div>
        <div class="p-2 text-xs font-semibold text-gray-400">過去のチャット履歴</div>
        <div id="sessions-list" class="flex-1 overflow-y-auto px-2 space-y-1"></div>
      </div>

      <!-- コンテンツ領域 -->
      <div class="flex-1 flex flex-col overflow-hidden relative">

        <!-- 1. AIチャットタブ -->
        <div id="view-chat" class="flex flex-col h-full">
          <div class="flex justify-between items-center px-6 py-2 border-b border-geminiBorder glass-card text-xs">
            <span class="text-gray-400">現在のモデル: <span id="current-model-badge" class="font-mono text-blue-400">openai/gpt-oss-120b</span></span>
            <span class="text-gray-500 text-[11px]">再生成は質問横、コピーは回答下から可能です</span>
          </div>

          <div id="chat-box" class="flex-1 overflow-y-auto p-6 space-y-6"></div>

          <div id="attachment-badge" class="hidden px-6 pt-2 pb-0 flex items-center space-x-2 text-xs">
            <span class="bg-gray-800 border border-gray-600 text-gray-200 px-2.5 py-1 rounded flex items-center space-x-1.5">
              <span>📎</span> <span id="attached-filename" class="font-mono max-w-xs truncate">file.txt</span>
              <button onclick="removeAttachment()" class="text-red-400 hover:text-red-300 ml-1 font-bold">×</button>
            </span>
          </div>

          <div class="p-4 glass-card border-t border-geminiBorder">
            <div class="max-w-4xl mx-auto flex items-end space-x-2">
              <input type="file" id="file-selector" class="hidden" onchange="handleFileSelected(event)">
              <button onclick="document.getElementById('file-selector').click()" title="ファイルを添付 (.kt, .java, .py, .txt, etc.)"
                class="p-2.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-xl border border-geminiBorder flex items-center justify-center">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"></path></svg>
              </button>

              <textarea id="chat-input" rows="2" placeholder="メッセージを入力 (Shift+Enterで改行、ファイルをここにドロップも可能)"
                class="flex-1 bg-gray-900/90 border border-geminiBorder rounded-xl p-3 text-sm focus:outline-none focus:border-blue-500 resize-none text-white"></textarea>

              <button id="send-btn" onclick="sendUserMessage()" class="px-6 py-3 bg-geminiBlue hover:bg-blue-600 text-white font-semibold rounded-xl text-sm transition shadow">送信</button>
            </div>
          </div>
        </div>

        <!-- 2. 🌟 AI コーディングタブ (Kotlin / Java / Mod開発対応) -->
        <div id="view-code" class="hidden flex h-full p-4 space-x-4 overflow-hidden">
          <!-- 左ペイン: AI指示・チャット -->
          <div class="w-2/5 flex flex-col glass-card border border-geminiBorder rounded-xl overflow-hidden">
            <div class="p-3 border-b border-geminiBorder font-semibold text-xs text-blue-400 flex justify-between items-center">
              <span>💬 コーディング相談・プロンプト</span>
            </div>
            <div id="code-chat-box" class="flex-1 p-4 overflow-y-auto space-y-3 text-xs">
              <div class="text-gray-400">Minecraft Mod開発、Kotlin / Java / Python / C++ などのコード生成・エラー解決・設計を相談できます。</div>
            </div>
            <div class="p-3 border-t border-geminiBorder flex flex-col space-y-2 bg-gray-900/50">
              <div class="flex flex-wrap gap-1.5 text-[11px]">
                <button onclick="quickCodeAction('error')" class="px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-red-300">🐞 エラー解決</button>
                <button onclick="quickCodeAction('convert')" class="px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-purple-300">🔄 Java ⇄ Kotlin</button>
                <button onclick="quickCodeAction('comment')" class="px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-green-300">📖 解説追加</button>
              </div>
              <div class="flex space-x-2">
                <textarea id="code-prompt-input" rows="2" placeholder="例: Fabric 1.20+でカスタムアイテムを追加するKotlinコードを書いて"
                  class="flex-1 bg-gray-900 border border-geminiBorder rounded-lg p-2 text-xs text-white focus:outline-none focus:border-blue-500 resize-none"></textarea>
                <button id="code-send-btn" onclick="sendCodePrompt()" class="px-4 bg-geminiBlue hover:bg-blue-600 text-white font-semibold rounded-lg text-xs shadow">生成</button>
              </div>
            </div>
          </div>

          <!-- 右ペイン: 多言語コードキャンバス -->
          <div class="flex-1 flex flex-col glass-card border border-geminiBorder rounded-xl overflow-hidden">
            <!-- ツールバー -->
            <div class="p-2.5 border-b border-geminiBorder flex justify-between items-center text-xs bg-gray-900/70">
              <div class="flex items-center space-x-2">
                <span class="text-gray-400 font-semibold">言語:</span>
                <select id="code-lang" class="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-xs" onchange="updateEditorSyntax()">
                  <option value="kotlin">Kotlin (.kt)</option>
                  <option value="java">Java (.java)</option>
                  <option value="gradle">Gradle (.gradle.kts)</option>
                  <option value="python">Python (.py)</option>
                  <option value="cpp">C++ (.cpp)</option>
                  <option value="csharp">C# (.cs)</option>
                  <option value="javascript">JavaScript (.js)</option>
                  <option value="json">JSON (.json)</option>
                </select>
              </div>
              <div class="space-x-2">
                <button onclick="saveCodeToFile()" class="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 text-white rounded font-medium shadow">💾 ファイル保存</button>
                <button onclick="copyCanvasCode(this)" class="px-3 py-1 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded font-medium">📋 コードをコピー</button>
              </div>
            </div>

            <!-- コード直接編集用エディタ -->
            <textarea id="code-editor" spellcheck="false" class="flex-1 p-4 bg-[#141517] text-gray-100 font-mono text-xs leading-relaxed focus:outline-none resize-none select-text"
              placeholder="// ここに生成されたコードが表示・直接編集できます&#10;package com.example.mod&#10;&#10;import net.minecraft.item.Item&#10;&#10;val CUSTOM_ITEM = Item(Item.Settings())"></textarea>
          </div>
        </div>

        <!-- 3. 数式計算機タブ -->
        <div id="view-calc" class="hidden flex h-full p-6 space-x-6 overflow-y-auto">
          <div class="flex-1 flex flex-col space-y-4">
            <div class="flex flex-wrap gap-2">
              <button onclick="insertFormula('factor()')" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs">因数分解: factor()</button>
              <button onclick="insertFormula('expand()')" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs">展開: expand()</button>
              <button onclick="insertFormula('solve(, x)')" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs">方程式解: solve(, x)</button>
              <button onclick="insertFormula('diff(, x)')" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs">微分: diff(, x)</button>
              <button onclick="insertFormula('integrate(, x)')" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs">積分: integrate(, x)</button>
            </div>

            <div class="flex space-x-3">
              <input type="text" id="calc-input" placeholder="数式を入力 (例: factor(x**2 - 5*x + 6))"
                class="flex-1 bg-gray-900 border border-geminiBorder rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-blue-500 font-mono">
              <button onclick="execCalc()" class="px-6 bg-geminiBlue hover:bg-blue-600 text-white font-semibold rounded-xl text-sm">計算実行</button>
            </div>

            <div class="flex-1 flex flex-col glass-card border border-geminiBorder rounded-xl p-4">
              <div class="flex justify-between items-center mb-2">
                <span class="text-xs font-semibold text-gray-400">計算結果:</span>
                <button onclick="copyCalcResult()" class="text-xs text-blue-400 hover:underline">📋 コピー</button>
              </div>
              <div id="calc-result" class="flex-1 font-mono text-base text-green-400 whitespace-pre-wrap select-text">ここに結果が表示されます</div>
            </div>
          </div>

          <div class="w-64 glass-card border border-geminiBorder rounded-xl p-4 flex flex-col">
            <span class="text-xs font-semibold text-gray-400 mb-2">履歴 (クリックで入力)</span>
            <div id="calc-history" class="flex-1 overflow-y-auto space-y-2 text-xs font-mono"></div>
          </div>
        </div>

        <!-- 4. 🌟 環境設定タブ (サブタブ: 一般 ＆ テーマ・壁紙) -->
        <div id="view-settings" class="hidden h-full p-8 max-w-2xl mx-auto overflow-y-auto space-y-6">
          <div class="flex items-center justify-between border-b border-geminiBorder pb-3">
            <h2 class="text-xl font-bold text-white">⚙️ 環境設定</h2>
            <!-- 設定サブタブ -->
            <div class="flex space-x-2 text-xs">
              <button onclick="switchSettingsSubTab('general')" id="set-subtab-general" class="px-3 py-1.5 rounded-lg bg-geminiBlue text-white font-medium">一般設定</button>
              <button onclick="switchSettingsSubTab('theme')" id="set-subtab-theme" class="px-3 py-1.5 rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700 font-medium">🎨 テーマ・壁紙</button>
            </div>
          </div>

          <!-- サブタブ1: 一般設定 -->
          <div id="settings-general-view" class="space-y-4 text-sm">
            <div>
              <label class="block font-semibold text-gray-300 mb-1">Groq API Key</label>
              <input type="password" id="set-api-key" class="w-full bg-gray-900 border border-geminiBorder rounded-lg p-2.5 text-white">
            </div>
            <div>
              <label class="block font-semibold text-gray-300 mb-1">使用モデル (LLM)</label>
              <select id="set-model" class="w-full bg-gray-900 border border-geminiBorder rounded-lg p-2.5 text-white">
                <option value="openai/gpt-oss-120b">openai/gpt-oss-120b (高精度・新標準)</option>
                <option value="openai/gpt-oss-20b">openai/gpt-oss-20b (超爆速)</option>
              </select>
            </div>
            <div class="flex items-center space-x-3 pt-2">
              <input type="checkbox" id="set-web-search" class="w-4 h-4 accent-blue-600 rounded">
              <label for="set-web-search" class="font-semibold text-gray-300">Web検索機能（DuckDuckGo）を有効にする</label>
            </div>
          </div>

          <!-- サブタブ2: 🎨 テーマ・壁紙設定 -->
          <div id="settings-theme-view" class="hidden space-y-5 text-sm">
            <div class="p-4 bg-gray-900/60 border border-geminiBorder rounded-xl space-y-3">
              <span class="block font-semibold text-gray-200">■ カスタム背景画像 (壁紙)</span>
              <div class="flex items-center space-x-3">
                <input type="file" id="wallpaper-selector" accept="image/*" class="hidden" onchange="handleWallpaperSelected(event)">
                <button onclick="document.getElementById('wallpaper-selector').click()" class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium text-xs shadow">📁 画像ファイルを選択</button>
                <button onclick="clearWallpaper()" class="px-3 py-2 bg-gray-800 hover:bg-gray-700 text-red-400 rounded-lg text-xs">✕ 壁紙を解除</button>
              </div>
              <div id="wallpaper-status" class="text-xs text-gray-400">現在、壁紙は設定されていません。</div>
            </div>

            <!-- すりガラス効果の調整 -->
            <div class="space-y-3 p-4 bg-gray-900/60 border border-geminiBorder rounded-xl">
              <div>
                <div class="flex justify-between text-xs font-semibold text-gray-300 mb-1">
                  <span>背景の暗さ（文字の読みやすさ）:</span>
                  <span id="overlay-val-text">75%</span>
                </div>
                <input type="range" id="set-overlay-opacity" min="0.3" max="0.95" step="0.05" value="0.75" oninput="applyThemeVisuals()" class="w-full">
              </div>

              <div>
                <div class="flex justify-between text-xs font-semibold text-gray-300 mb-1">
                  <span>すりガラスのぼかし効果 (Blur):</span>
                  <span id="blur-val-text">8px</span>
                </div>
                <input type="range" id="set-blur-val" min="0" max="20" step="2" value="8" oninput="applyThemeVisuals()" class="w-full">
              </div>
            </div>
          </div>

          <button onclick="saveSettings()" class="mt-4 px-6 py-2.5 bg-green-600 hover:bg-green-500 font-semibold text-white rounded-lg transition shadow">💾 設定を保存して反映</button>
        </div>

      </div>
    </div>
  </div>

  <script>
    let isDark = true;
    let attachedFile = { name: "", content: "" };
    let currentWallpaperData = "";
    const turnAnswers = {};

    function toggleSidebar() {
      document.getElementById('sidebar').classList.toggle('-ml-64');
    }

    function switchTab(tab) {
      ['chat', 'code', 'calc', 'settings'].forEach(t => {
        document.getElementById('view-' + t).classList.add('hidden');
        document.getElementById('tab-' + t).className = "px-4 py-1.5 text-xs font-semibold rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700";
      });
      document.getElementById('view-' + tab).classList.remove('hidden');
      document.getElementById('tab-' + tab).className = "px-4 py-1.5 text-xs font-semibold rounded-lg bg-geminiBlue text-white shadow";
    }

    function switchSettingsSubTab(sub) {
      ['general', 'theme'].forEach(s => {
        document.getElementById('settings-' + s + '-view').classList.add('hidden');
        document.getElementById('set-subtab-' + s).className = "px-3 py-1.5 rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700 font-medium";
      });
      document.getElementById('settings-' + sub + '-view').classList.remove('hidden');
      document.getElementById('set-subtab-' + sub).className = "px-3 py-1.5 rounded-lg bg-geminiBlue text-white font-medium";
    }

    function toggleTheme() {
      isDark = !isDark;
      document.documentElement.classList.toggle('dark', isDark);
      document.getElementById('theme-btn').innerText = isDark ? "☀️ ライト" : "🌙 ダーク";
      applyThemeVisuals();
    }

    // 🌟 壁紙 ＆ すりガラス効果の描画
    function applyThemeVisuals() {
      const bgLayer = document.getElementById('bg-layer');
      const glassOverlay = document.getElementById('glass-overlay');
      const opacity = parseFloat(document.getElementById('set-overlay-opacity').value);
      const blurVal = parseInt(document.getElementById('set-blur-val').value);

      document.getElementById('overlay-val-text').innerText = Math.round(opacity * 100) + "%";
      document.getElementById('blur-val-text').innerText = blurVal + "px";

      if (currentWallpaperData) {
        bgLayer.style.backgroundImage = `url('${currentWallpaperData}')`;
        glassOverlay.style.backgroundColor = isDark ? `rgba(19, 19, 20, ${opacity})` : `rgba(245, 245, 247, ${opacity})`;
        glassOverlay.style.backdropFilter = `blur(${blurVal}px)`;
        document.documentElement.style.setProperty('--glass-blur', blurVal + 'px');
        document.documentElement.style.setProperty('--glass-card-alpha', opacity);
      } else {
        bgLayer.style.backgroundImage = 'none';
        glassOverlay.style.backgroundColor = 'transparent';
        glassOverlay.style.backdropFilter = 'none';
        document.documentElement.style.setProperty('--glass-blur', '0px');
        document.documentElement.style.setProperty('--glass-card-alpha', '1');
      }
    }

    function handleWallpaperSelected(e) {
      const file = e.target.files[0];
      if (!file) return;

      const reader = new FileReader();
      reader.onload = (evt) => {
        currentWallpaperData = evt.target.result;
        document.getElementById('wallpaper-status').innerText = `✔ 壁紙適用中: ${file.name}`;
        applyThemeVisuals();
      };
      reader.readAsDataURL(file);
    }

    function clearWallpaper() {
      currentWallpaperData = "";
      document.getElementById('wallpaper-status').innerText = "現在、壁紙は設定されていません。";
      applyThemeVisuals();
    }

    // --- セッション履歴 ---
    async function refreshSessionsList() {
      const data = await pywebview.api.get_sessions_list();
      const listDiv = document.getElementById('sessions-list');
      listDiv.innerHTML = '';

      data.sessions.forEach(s => {
        const item = document.createElement('div');
        const isActive = s.id === data.current_id;
        item.className = `group flex items-center justify-between p-2 rounded-lg cursor-pointer text-xs truncate transition ${
          isActive ? 'bg-geminiCard border border-geminiBorder text-blue-400 font-semibold' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
        }`;
        
        item.innerHTML = `
          <span class="truncate flex-1" onclick="switchSession('${s.id}')">💬 ${s.title}</span>
          <button onclick="deleteSession(event, '${s.id}')" title="削除" class="opacity-0 group-hover:opacity-100 hover:text-red-400 p-1">🗑️</button>
        `;
        listDiv.appendChild(item);
      });
    }

    async function switchSession(id) {
      const res = await pywebview.api.switch_session(id);
      if (res.status === 'success') {
        const chatBox = document.getElementById('chat-box');
        chatBox.innerHTML = '';
        if (res.messages.length === 0) {
          chatBox.innerHTML = '<div class="p-4 rounded-xl glass-card border border-geminiBorder text-gray-300 text-sm">新しいチャットです。何でも質問してください。</div>';
        } else {
          let lastTurnId = null;
          res.messages.forEach(m => {
            if (m.role === 'user') {
              lastTurnId = 'turn-' + Math.random().toString(36).substr(2, 9);
              appendUserMessage(m.content, lastTurnId, false);
            } else if (m.role === 'assistant') {
              appendAiMessage(m.content, lastTurnId, false);
            }
          });
        }
        await refreshSessionsList();
      }
    }

    async function startNewChat() {
      await pywebview.api.create_new_session();
      await switchSession((await pywebview.api.get_sessions_list()).current_id);
    }

    async function deleteSession(e, id) {
      e.stopPropagation();
      if (confirm('このチャット履歴を削除しますか？')) {
        await pywebview.api.delete_session(id);
        const cur = (await pywebview.api.get_sessions_list()).current_id;
        await switchSession(cur);
      }
    }

    // --- ファイル添付ハンドラー ---
    function handleFileSelected(e) {
      const file = e.target.files[0];
      if (!file) return;

      const reader = new FileReader();
      reader.onload = (evt) => {
        attachedFile.name = file.name;
        attachedFile.content = evt.target.result;
        document.getElementById('attached-filename').innerText = file.name;
        document.getElementById('attachment-badge').classList.remove('hidden');

        // コーディングタブの言語も自動推定
        const ext = file.name.split('.').pop().toLowerCase();
        const langMap = { 'kt': 'kotlin', 'java': 'java', 'py': 'python', 'cpp': 'cpp', 'js': 'javascript', 'gradle': 'gradle' };
        if (langMap[ext]) {
          document.getElementById('code-lang').value = langMap[ext];
          document.getElementById('code-editor').value = evt.target.result;
        }
      };
      reader.readAsText(file);
    }

    function removeAttachment() {
      attachedFile = { name: "", content: "" };
      document.getElementById('attachment-badge').classList.add('hidden');
      document.getElementById('file-selector').value = "";
    }

    // --- AIチャット送信 ---
    async function sendUserMessage() {
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text && !attachedFile.content) return;

      let displayText = text;
      if (attachedFile.name) {
        displayText = `📎 **[添付: ${attachedFile.name}]**\\n` + (text || "このファイルを解説・要約してください。");
      }

      const turnId = 'turn-' + Date.now();
      appendUserMessage(displayText, turnId);
      input.value = '';

      const fName = attachedFile.name;
      const fCont = attachedFile.content;
      removeAttachment();

      const sendBtn = document.getElementById('send-btn');
      sendBtn.disabled = true;
      sendBtn.innerText = "考え中...";

      const placeholder = appendAiMessage('<span class="animate-pulse">思考中...</span>', turnId);

      const res = await pywebview.api.send_message(text || "このファイルを解説・処理してください。", fName, fCont);
      sendBtn.disabled = false;
      sendBtn.innerText = "送信";

      if (res.status === 'success') {
        updateAiMessage(placeholder, res.reply, turnId);
        await refreshSessionsList();
      } else {
        updateAiMessage(placeholder, "⚠️ エラー: " + res.message, turnId);
      }
    }

    function appendUserMessage(text, turnId, shouldScroll = true) {
      const box = document.getElementById('chat-box');
      const div = document.createElement('div');
      div.id = 'user-container-' + turnId;
      div.className = "flex justify-end items-end space-x-2 my-2";

      div.innerHTML = `
        <button onclick="regenerateTurnAnswer('${turnId}')" class="text-gray-400 hover:text-gray-200 text-xs px-1.5 py-1 transition select-none opacity-70 hover:opacity-100 flex items-center space-x-1 mb-1 cursor-pointer" title="この質問への回答を再生成">
          <span>🔄 再生成</span>
        </button>
        <div class="max-w-3xl bg-blue-600/90 text-white p-3.5 rounded-2xl rounded-tr-none text-sm shadow whitespace-pre-wrap select-text">
          ${text}
        </div>
      `;

      box.appendChild(div);
      if (shouldScroll) box.scrollTop = box.scrollHeight;
    }

    function appendAiMessage(text, turnId, shouldScroll = true) {
      const box = document.getElementById('chat-box');
      const div = document.createElement('div');
      div.id = 'ai-container-' + turnId;
      div.className = "flex flex-col items-start my-2 max-w-3xl";

      div.innerHTML = `
        <div id="ai-bubble-${turnId}" class="ai-bubble w-full glass-card border border-geminiBorder text-gray-200 p-4 rounded-2xl rounded-tl-none text-sm prose dark:prose-invert shadow select-text">
          ${renderMarkdown(text)}
        </div>
        <div class="flex items-center space-x-2 text-gray-400 text-xs mt-1.5 pl-1 select-none">
          <button id="copy-btn-${turnId}" onclick="copyAnswerText('${turnId}', this)" class="hover:text-gray-200 transition flex items-center space-x-1 py-0.5 px-1.5 rounded cursor-pointer opacity-70 hover:opacity-100">
            <span>📋 回答をコピー</span>
          </button>
        </div>
      `;

      if (turnId && !text.includes('思考中...')) {
        turnAnswers[turnId] = text;
      }

      box.appendChild(div);
      if (shouldScroll) box.scrollTop = box.scrollHeight;
      return div.querySelector('.ai-bubble');
    }

    function updateAiMessage(el, text, turnId) {
      turnAnswers[turnId] = text;
      el.innerHTML = renderMarkdown(text);
      document.getElementById('chat-box').scrollTop = document.getElementById('chat-box').scrollHeight;
    }

    function copyAnswerText(turnId, btn) {
      let textToCopy = turnAnswers[turnId];
      if (!textToCopy) {
        const bubble = document.getElementById('ai-bubble-' + turnId);
        if (bubble) textToCopy = bubble.innerText;
      }
      if (!textToCopy) return;

      const ta = document.createElement('textarea');
      ta.value = textToCopy;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);

      const orig = btn.innerHTML;
      btn.innerHTML = "<span class='text-emerald-400 font-medium'>✔ コピーしました</span>";
      setTimeout(() => btn.innerHTML = orig, 1500);
    }

    async function regenerateTurnAnswer(turnId) {
      const bubble = document.getElementById('ai-bubble-' + turnId);
      if (!bubble) return;

      bubble.innerHTML = '<span class="animate-pulse">回答を再生成中...</span>';
      const res = await pywebview.api.regenerate_response();
      if (res.status === 'success') {
        updateAiMessage(bubble, res.reply, turnId);
      } else {
        updateAiMessage(bubble, "⚠️ " + res.message, turnId);
      }
    }

    // 🌟 AI コーディングタブの処理 (Kotlin / Java等)
    async function sendCodePrompt(overridePrompt = null) {
      const input = document.getElementById('code-prompt-input');
      const instruction = overridePrompt || input.value.trim();
      const currentCode = document.getElementById('code-editor').value;
      const lang = document.getElementById('code-lang').value;

      if (!instruction) return;
      if (!overridePrompt) input.value = '';

      const chatBox = document.getElementById('code-chat-box');
      const userDiv = document.createElement('div');
      userDiv.className = "p-2 bg-blue-600/70 text-white rounded-lg";
      userDiv.innerText = instruction;
      chatBox.appendChild(userDiv);

      const aiDiv = document.createElement('div');
      aiDiv.className = "p-2.5 glass-card border border-geminiBorder text-gray-200 rounded-lg";
      aiDiv.innerHTML = '<span class="animate-pulse text-blue-400">コードを生成・解析中...</span>';
      chatBox.appendChild(aiDiv);
      chatBox.scrollTop = chatBox.scrollHeight;

      const res = await pywebview.api.send_code_prompt(instruction, currentCode, lang);

      if (res.status === 'success') {
        aiDiv.innerHTML = renderMarkdown(res.reply);
        // 生成コードを自動抽出して右側エディタへ展開
        const codeMatch = res.reply.match(/```(?:[a-zA-Z0-9_\-]+)?\n([\s\S]*?)```/);
        if (codeMatch && codeMatch[1]) {
          document.getElementById('code-editor').value = codeMatch[1].trim();
        }
      } else {
        aiDiv.innerHTML = "⚠️ " + res.message;
      }
      chatBox.scrollTop = chatBox.scrollHeight;
    }

    function quickCodeAction(type) {
      const lang = document.getElementById('code-lang').value;
      if (type === 'error') {
        sendCodePrompt(`この【${lang}】コードに構文エラーやバグがないか診断し、完全に修正されたコードを書いてください。`);
      } else if (type === 'convert') {
        const target = lang === 'kotlin' ? 'Java' : 'Kotlin';
        sendCodePrompt(`このコードを【${target}】に書き換えてください。マイクラMod開発や慣習に沿った書き方にしてください。`);
        document.getElementById('code-lang').value = target.toLowerCase();
      } else if (type === 'comment') {
        sendCodePrompt(`このコードの重要な処理やイベント、引数に親切で詳しい日本語のJavadoc/コメントを追加してください。`);
      }
    }

    function copyCanvasCode(btn) {
      const code = document.getElementById('code-editor').value;
      const ta = document.createElement('textarea');
      ta.value = code;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);

      const orig = btn.innerText;
      btn.innerText = "✔ コピー済";
      setTimeout(() => btn.innerText = orig, 1500);
    }

    function saveCodeToFile() {
      const code = document.getElementById('code-editor').value;
      const lang = document.getElementById('code-lang').value;
      const extMap = { 'kotlin': 'kt', 'java': 'java', 'gradle': 'gradle.kts', 'python': 'py', 'cpp': 'cpp', 'csharp': 'cs', 'javascript': 'js', 'json': 'json' };
      const ext = extMap[lang] || 'txt';

      const blob = new Blob([code], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `ModCode.${ext}`;
      a.click();
      URL.revokeObjectURL(a.href);
    }

    function renderMarkdown(content) {
      let html = marked.parse(content);
      setTimeout(() => {
        renderMathInElement(document.body, {
          delimiters: [{left: '$$', right: '$$', display: true}, {left: '$', right: '$', display: false}],
          throwOnError: false
        });
        document.querySelectorAll('pre code').forEach((el) => {
          hljs.highlightElement(el);
          const pre = el.parentElement;
          if (!pre.querySelector('.copy-btn')) {
            const btn = document.createElement('button');
            btn.className = 'copy-btn';
            btn.innerText = '📋 コピー';
            btn.onclick = () => {
              const ta = document.createElement('textarea');
              ta.value = el.innerText;
              document.body.appendChild(ta);
              ta.select();
              document.execCommand('copy');
              document.body.removeChild(ta);
              btn.innerHTML = '<span class="text-emerald-400">✔ コピー済</span>';
              setTimeout(() => btn.innerText = '📋 コピー', 1500);
            };
            pre.appendChild(btn);
          }
        });
      }, 50);
      return html;
    }

    // 計算機
    async function execCalc() {
      const expr = document.getElementById('calc-input').value.trim();
      if (!expr) return;
      const res = await pywebview.api.calculate_sympy(expr);
      document.getElementById('calc-result').innerText = res.result;

      const hist = document.getElementById('calc-history');
      const item = document.createElement('div');
      item.className = "p-2 bg-gray-900 rounded hover:bg-gray-800 cursor-pointer truncate";
      item.innerText = expr;
      item.onclick = () => document.getElementById('calc-input').value = expr;
      hist.prepend(item);
    }

    function insertFormula(cmd) {
      const inp = document.getElementById('calc-input');
      inp.value += cmd;
      inp.focus();
    }

    function copyCalcResult() {
      const text = document.getElementById('calc-result').innerText;
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      alert('計算結果をコピーしました！');
    }

    // 設定ロード＆セーブ
    async function loadSettings() {
      const s = await pywebview.api.get_settings();
      document.getElementById('set-api-key').value = s.api_key;
      document.getElementById('set-model').value = s.model;
      document.getElementById('set-web-search').checked = s.web_search;
      document.getElementById('current-model-badge').innerText = s.model;

      if (s.wallpaper) {
        currentWallpaperData = s.wallpaper;
        document.getElementById('wallpaper-status').innerText = "✔ 保存済み壁紙を適用中";
      }
      document.getElementById('set-overlay-opacity').value = s.overlay_opacity || 0.75;
      document.getElementById('set-blur-val').value = s.blur_val !== undefined ? s.blur_val : 8;

      applyThemeVisuals();
      await refreshSessionsList();
      const list = await pywebview.api.get_sessions_list();
      if (list.current_id) {
        await switchSession(list.current_id);
      }
    }

    async function saveSettings() {
      const key = document.getElementById('set-api-key').value;
      const model = document.getElementById('set-model').value;
      const web = document.getElementById('set-web-search').checked;
      const opacity = document.getElementById('set-overlay-opacity').value;
      const blurVal = document.getElementById('set-blur-val').value;

      await pywebview.api.update_settings(key, model, web, currentWallpaperData, opacity, blurVal);
      document.getElementById('current-model-badge').innerText = model;
      alert('設定を保存しました！');
    }

    document.getElementById('chat-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendUserMessage();
      }
    });

    window.addEventListener('pywebviewready', loadSettings);
  </script>
</body>
</html>
"""


def main():
    api = BackendAPI()
    webview.create_window(
        title="LLMCalcJP",
        html=HTML_TEMPLATE,
        js_api=api,
        width=1250,
        height=820,
        min_size=(980, 680)
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()