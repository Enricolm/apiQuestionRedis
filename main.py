from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import redis
import time

app = FastAPI()

 
r = redis.Redis(host='localhost', port=6379, decode_responses=True)

QUESTION_TIME_LIMIT = 20

class Question(BaseModel):
    quiz_id: str
    question_text: str
    question_id: int
    correct_answer: str

class AnswerRequest(BaseModel):
    user_id: str
    quiz_id: str
    question_id: str
    answer: str

@app.post("/quiz/{quiz_id}/question")
def create_question(quiz_id: str, question: Question):
    question_key = f"quiz:{quiz_id}:question:{question.question_id}"
    if r.exists(question_key):
        return {"message": "Question already exists"}
    
    r.hset(question_key, mapping={
        'question_text': question.question_text,
        'correct_answer': question.correct_answer
    })
    r.expire(question_key, 2592000)
    
    return {"message": "Question has been created"}

@app.get("/quiz/{quiz_id}/question/{question_id}")
def get_question(quiz_id: str, question_id: str):
    return r.hgetall(f"quiz:{quiz_id}:question:{question_id}")

@app.get("/quiz/{quiz_id}/question/{question_id}/start")
def get_question_with_timer(user_id: str, quiz_id: str, question_id: str):
    question = r.hgetall(f"quiz:{quiz_id}:question:{question_id}")
    if not question:
        raise HTTPException(status_code=404, detail="Pergunta não encontrada")

    r.setex(f"user:{user_id}:quiz:{quiz_id}:question_time:{question_id}", QUESTION_TIME_LIMIT, int(time.time()))
    
    return {"question": question, "start_time": int(time.time())}

@app.post("/quiz/{quiz_id}/answer/")
def answer_question(data: AnswerRequest):
    vote_key = f"quiz:{data.quiz_id}:votes:{data.question_id}"
    user_vote_key = f"user:{data.user_id}:quiz:{data.quiz_id}:answered"
    response_time_key = f"quiz:{data.quiz_id}:response_time:{data.question_id}:{data.user_id}"

    start_time = r.get(f"user:{data.user_id}:quiz:{data.quiz_id}:question_time:{data.question_id}")
    if not start_time:
        raise HTTPException(status_code=400, detail="Tempo para resposta expirado!")
    
    elapsed_time = int(time.time()) - int(start_time)
    if elapsed_time > QUESTION_TIME_LIMIT:
        raise HTTPException(status_code=400, detail="Resposta enviada após o tempo limite!")

    if r.sismember(user_vote_key, data.question_id):
        raise HTTPException(status_code=400, detail="Você já respondeu essa pergunta!")
    
    correct_answer = r.hget(f"quiz:{data.quiz_id}:question:{data.question_id}", "correct_answer")
    is_correct = int(data.answer == correct_answer)
    
    r.hincrby(vote_key, data.answer, 1)
    r.sadd(user_vote_key, data.question_id)
    r.zincrby(f"quiz:{data.quiz_id}:rankings:correct", is_correct, data.user_id)
    r.zadd(f"quiz:{data.quiz_id}:rankings:fastest", {data.user_id: elapsed_time})
    r.zincrby(f"quiz:{data.quiz_id}:rankings:correct_fastest", is_correct / (elapsed_time + 1), data.user_id)
    r.setex(response_time_key, 2592000, elapsed_time)
    
    return {"message": "Resposta registrada!", "tempo_de_resposta": elapsed_time}

@app.get("/quiz/{quiz_id}/rankings")
def get_rankings(quiz_id: str):
    vote_keys = r.keys(f"quiz:{quiz_id}:votes:*")
    votes = {key.split(":")[-1]: r.hgetall(key) for key in vote_keys}
    
    response_times = r.keys(f"quiz:{quiz_id}:response_time:*")
    response_time_values = [int(r.get(key)) for key in response_times]
    
    rankings = {
        "alternativas_mais_votadas": votes,
        "tempo_medio_resposta": sum(response_time_values) / max(1, len(response_time_values)),
        "alunos_mais_rapidos": r.zrange(f"quiz:{quiz_id}:rankings:fastest", 0, -1, withscores=True),
        "questoes_mais_acertadas": sorted(votes.items(), key=lambda x: max(map(int, x[1].values())), reverse=True),
        "questoes_mais_abstencoes": sorted(votes.items(), key=lambda x: sum(map(int, x[1].values()))),
        "alunos_com_mais_acertos": r.zrange(f"quiz:{quiz_id}:rankings:correct", 0, -1, withscores=True),
        "alunos_com_mais_acertos_rapidos": r.zrange(f"quiz:{quiz_id}:rankings:correct_fastest", 0, -1, withscores=True)
    }

    return rankings