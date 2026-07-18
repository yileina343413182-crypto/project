"""API 端点测试脚本"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:5000"

def get(path):
    url = BASE + path
    print("GET", url)
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            print(json.dumps(data, ensure_ascii=False, indent=2)[:500])
    except Exception as e:
        print("ERROR:", e)
    print()

def post(path, body):
    url = BASE + path
    print("POST", url)
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            print(json.dumps(result, ensure_ascii=False, indent=2)[:500])
    except Exception as e:
        print("ERROR:", e)
    print()

print("=" * 60)
print("1. 健康检查")
get("/api/health")

print("2. 动漫列表")
get("/api/anime/list")

print("3. 评论分页 (anime_id=2, page=1, size=3)")
get("/api/comments/2?page=1&size=3")

print("4. 评论按情感过滤 (anime_id=2, sentiment=positive)")
get("/api/comments/2?sentiment=positive&page=1&size=2")

print("5. 情感统计 (anime_id=2)")
get("/api/sentiment/stats/2")

print("6. 情感趋势 (anime_id=2)")
get("/api/sentiment/trend/2")

print("7. LDA主题 (anime_id=1)")
get("/api/topics/1")

print("8. 词云数据 (anime_id=2)")
get("/api/wordcloud/2")

print("9. 实时预测 (textcnn)")
post("/api/sentiment/predict", {"text": "这部动漫太好看了，剧情超级感人", "model": "textcnn"})

print("10. 404 测试")
get("/api/nonexistent")

print("=" * 60)
print("全部测试完成！")
