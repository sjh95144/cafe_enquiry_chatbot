import os
import pandas as pd
import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    nickname: str
    message: str
    history: list
    state: str  # 'chat' 또는 'verify'

API_KEY = "AIzaSyCiTwPhnTmxpeTjTaZ4RTUKSXEy5NMSSAQ"
MODEL_NAME = 'gemini-2.5-flash'

class GeminiCafeCore:
    def __init__(self, api_key, menu_path='테스트용 가게 메뉴.csv'):
        self.client = genai.Client(api_key=api_key)
        
        if os.path.exists(menu_path):
            df = pd.read_csv(menu_path)
            menu_list = []
            for _, row in df.iterrows():
                note = f" ({row['비고']})" if pd.notna(row['비고']) else ""
                menu_list.append(f"- {row['상품명']}: {row['가격']}원 | 추천옵션: {row['추천 옵션']}{note}")
            self.menu_info = "\n".join(menu_list)
        else:
            self.menu_info = "현재 등록된 매장 메뉴 정보가 없습니다."

    def ask(self, nickname, user_input, history):
        history_text = "\n".join(history[-10:])
        prompt = f"""
        당신은 친절하고 계산이 정확한 한국 카페 사장님입니다. 손님의 질문에 다정하게 대화하듯 답변하세요.

        [매장 정보 및 규칙]
        1. 아래 [매장 메뉴판]의 각 메뉴 옆 괄호() 안에 적힌 내용(예: 2샷)은 그 메뉴의 기본 구성 정보입니다. 손님이 기본 구성을 물어보면 이를 바탕으로 정확하게 안내하세요.
        2. 고객의 닉네임 뒤에는 반드시 '님'을 한 번만 붙이세요. (예: {nickname}님)
        3. 손님이 여러 메뉴를 주문하거나 총 합산 금액을 물어보면, [매장 메뉴판]의 가격을 바탕으로 정확하게 암산하여 최종 합계를 다정하게 확인해 주세요.
        4. 답변할 때 웃음 표시(예: ^^, ㅎㅎ)나 이모지(😊, ☕)를 절대로 사용하지 마세요.

        [매장 메뉴판]
        {self.menu_info}
        - 샷 추가: 500원

        [이전 대화 기록]
        {history_text}

        현재 손님({nickname}): {user_input}
        사장님 답변:"""
        
        response = self.client.models.generate_content(
            model=MODEL_NAME, contents=prompt, config=types.GenerateContentConfig(temperature=0.3)
        )
        return response.text.strip()

    def generate_interim_report(self, history):
        full_history = "\n".join(history)
        report_prompt = f"""
        당신은 카페 운영 매니저입니다. 다음 대화 내용을 바탕으로 손님이 요구하거나 물어본 핵심 사항을 100% 한글로 요약하세요.
        영어나 외국어, 이모티콘을 절대 섞지 마세요.

        출력 형식:
        요약: (손님이 문의하거나 주문한 내용 요약)
        니즈: (손님이 최종적으로 확인받고 싶어 한 정보나 목적 핵심 요약)

        [대화 내용]
        {full_history}
        """
        response = self.client.models.generate_content(
            model=MODEL_NAME, contents=report_prompt, config=types.GenerateContentConfig(temperature=0.1)
        )
        analysis = response.text.strip()
        
        summary, needs = "요약 파싱 실패", "니즈 파싱 실패"
        for line in analysis.split('\n'):
            if line.startswith('요약:'): summary = line.replace('요약:', '').strip()
            elif line.startswith('니즈:'): needs = line.replace('니즈:', '').strip()
            
        return {"summary": summary, "needs": needs}

    def save_to_csv(self, nickname, history, summary, needs, log_path='customer_logs.csv'):
        full_history = "\n".join(history)
        log_data = {
            '접수시간': [datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            '닉네임': [nickname],
            '요약본': [summary],
            '고객 니즈': [needs],
            '대화 전문': [full_history]
        }
        df_log = pd.DataFrame(log_data)
        if not os.path.exists(log_path):
            df_log.to_csv(log_path, index=False, encoding='utf-8-sig')
        else:
            df_log.to_csv(log_path, mode='a', header=False, index=False, encoding='utf-8-sig')

bot_core = GeminiCafeCore(api_key=API_KEY)

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    try:
        # 안전장치: 대화 상태와 상관없이 '나가기' 단어가 들어오면 무조건 요약 검증 모드로 진입
        if req.message == "나가기":
            report_data = bot_core.generate_interim_report(req.history)
            return {
                "status": "verify_request",
                "reply": f"정리된 문의 내용이 맞으신가요?<br><br><b>[요약]</b> {report_data['summary']}<br><b>[니즈]</b> {report_data['needs']}<br><br>내용이 정확하다면 <b>'네'</b>, 틀리다면 <b>'아니오'</b>를 입력해 주세요."
            }
            
        # 검증(Y/N 대답 기다리는) 상태 처리
        if req.state == 'verify':
            if "네" in req.message:
                report_data = bot_core.generate_interim_report(req.history)
                bot_core.save_to_csv(req.nickname, req.history, report_data["summary"], report_data["needs"])
                return {
                    "status": "terminated",
                    "reply": "감사합니다. 대화가 정식 종료되었으며 문의 내용이 데이터베이스 장부에 안전하게 접수되었습니다. 다음에 또 찾아주세요!"
                }
            elif "아니오" in req.message or "아니요" in req.message:
                return {
                    "status": "resume_chat",
                    "reply": "내용이 다르셨군요! 다시 정상 대화 상담 모드로 돌아갑니다. 무엇을 더 도와드릴까요?"
                }
            else:
                return {
                    "status": "keep_verify",
                    "reply": "죄송하지만 올바른 답변이 아닙니다. 위의 내용이 정확하다면 <b>'네'</b>, 수정이 필요하면 <b>'아니오'</b>라고 입력해 주세요."
                }
                
        # 일반 대화 응대
        reply = bot_core.ask(req.nickname, req.message, req.history)
        return {"status": "chatting", "reply": reply}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)