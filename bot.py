import discord
from discord.ext import commands, tasks
import aiohttp
import json
import re
import os
import html
import datetime
from datetime import time, timezone, timedelta
import random
import traceback

# ================= [ 설정 구역 ] =================
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# 아래 채널 ID를 본인 서버에 맞게 설정해 주세요!
NEWS_CHANNEL_ID = 1480944831600656384  
VOTE_CHANNEL_ID = 1484797598241128598  
YT_NOTI_CHANNEL_ID = 1487481812874825879
REDDIT_NOTI_CHANNEL_ID = 1487488570791821443

KST = timezone(timedelta(hours=9))
SCHEDULED_VOTE_TIME = time(hour=13, minute=0, second=0, tzinfo=KST)

TIER_DATA = {
    "Challenger": 0xf4c874, "Grandmaster": 0xc64444, "Master": 0x9d5ca3,
    "Diamond": 0x576bce, "Emerald": 0x2da161, "Platinum": 0x4e9996,
    "Gold": 0xcd8837, "Silver": 0x80989d, "Bronze": 0x8c513a,
    "Iron": 0x51484a, "Unranked": 0x000000
}
TIER_LIST = list(TIER_DATA.keys())
pending_users = {}

def log(message):
    now = datetime.datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] {message}", flush=True)

# =================[ 봇 클래스 정의 ] =================
class LoLBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True 
        super().__init__(command_prefix='!', intents=intents)
        self.session = None

    async def setup_hook(self):
        # 레딧은 봇 User-Agent를 고유하게 적어주는 것을 권장/요구합니다.
        self.session = aiohttp.ClientSession(headers={
            "User-Agent": "linux:lol-support-bot:v1.0 (by /u/YourRedditUsername)"
        })
        log("공용 HTTP 세션 생성 및 봇 준비 완료")
        
    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

bot = LoLBot()

# =================[ 공통 유틸리티 함수 ] =================
async def get_puuid(name_with_tag):
    if "#" not in name_with_tag: return None
    try:
        name, tag = name_with_tag.split("#", 1)
        url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name.strip()}/{tag.strip()}?api_key={RIOT_API_KEY}"
        async with bot.session.get(url) as resp:
            if resp.status == 200:
                return (await resp.json()).get('puuid')
    except Exception as e:
        log(f"PUUID 조회 에러: {e}")
    return None

async def get_recent_posted_links(channel, limit=100):
    """채널 최근 히스토리에서 봇이 올린 임베드 URL 목록을 한 번에 가져옴"""
    links =[]
    async for msg in channel.history(limit=limit):
        if msg.author == bot.user and msg.embeds and msg.embeds[0].url:
            links.append(msg.embeds[0].url)
    return links

# ================= [ 핵심 기능 1: 공식 홈페이지 뉴스 ] =================
async def fetch_and_post_news():
    log("롤 뉴스 체크 중...")
    url = "https://www.leagueoflegends.com/ko-kr/news/"
    
    # 롤 공홈용 일반 User-Agent
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        async with bot.session.get(url, headers=headers) as resp:
            if resp.status != 200: return
            raw_html = await resp.text()
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', raw_html)
            if not match: return
            
            data = json.loads(match.group(1))
            blades = data.get('props', {}).get('pageProps', {}).get('page', {}).get('blades', [])
            articles = next((b['items'] for b in blades if b.get('type') == 'articleCardGrid'), [])[:10]
            articles.reverse()

            channel = await bot.fetch_channel(NEWS_CHANNEL_ID)
            posted_links = await get_recent_posted_links(channel, limit=100)
            
            for art in articles:
                link = art.get('action', {}).get('payload', {}).get('url', '')
                if not link: continue
                if not link.startswith('http'): link = "https://www.leagueoflegends.com" + link
                
                if link in posted_links: continue

                title = art.get('title', '새로운 소식')
                desc = re.sub(r'<[^>]+>', '', art.get('description', {}).get('body', '')).strip()
                img = html.unescape(art.get('media', {}).get('url', '')).strip()
                
                pub = art.get('publishedAt', '')
                date_text = "새 소식"
                if pub:
                    dt = datetime.datetime.fromisoformat(pub.replace('Z', '+00:00')).astimezone(KST)
                    date_text = dt.strftime("%Y년 %m월 %d일 %H:%M")

                embed = discord.Embed(title=title, url=link, description=desc[:100], color=0xFFFFFF)
                if img.startswith('http'): embed.set_image(url=img)
                embed.set_footer(text=date_text)
                await channel.send(embed=embed)
                log(f"뉴스 포스팅: {title}")
                posted_links.append(link) 
                
    except Exception as e: log(f"뉴스 에러: {e}")

# =================[ 핵심 기능 2: 유튜브 재생목록 ] =================
async def fetch_and_post_youtube():
    log("유튜브 영상 체크 중...")
    UPLOADS_PLAYLIST_ID = "UUooLkG0FfrkPBQsSuC95L6w"
    
    if not YOUTUBE_API_KEY:
        log("유튜브 API 키 부재")
        return
        
    url = f"https://www.googleapis.com/youtube/v3/playlistItems?key={YOUTUBE_API_KEY}&playlistId={UPLOADS_PLAYLIST_ID}&part=snippet&maxResults=10"
    try:
        async with bot.session.get(url) as resp:
            if resp.status != 200: return
            videos = (await resp.json()).get('items',[])
            videos.reverse()

            channel = await bot.fetch_channel(YT_NOTI_CHANNEL_ID)
            posted_links = await get_recent_posted_links(channel, limit=100)
            
            for vid in videos:
                v_id = vid.get('snippet', {}).get('resourceId', {}).get('videoId')
                if not v_id: continue
                
                v_url = f"https://www.youtube.com/watch?v={v_id}"
                if v_url in posted_links: continue

                title = html.unescape(vid['snippet']['title'])
                desc = vid['snippet']['description'][:100] + "..."
                
                thumbnails = vid['snippet']['thumbnails']
                img = thumbnails.get('maxres', thumbnails.get('high', {})).get('url', '')
                
                dt = datetime.datetime.fromisoformat(vid['snippet']['publishedAt'].replace('Z', '+00:00')).astimezone(KST)

                embed = discord.Embed(title=title, url=v_url, description=desc, color=0xFF0000)
                if img: embed.set_image(url=img)
                embed.set_footer(text=f"{dt.strftime('%Y년 %m월 %d일 %H:%M')}")
                
                await channel.send(embed=embed)
                log(f"유튜브 포스팅: {title}")
                posted_links.append(v_url)
                
    except Exception as e: log(f"유튜브 에러: {e}")

# =================[ 핵심 기능 3: 레딧(Reddit) 스크래핑 ] =================
async def fetch_and_post_reddit():
    log("레딧(Reddit) 게시물 방어벽 확인 없이 바로 긁어오기 시도 중...")
    
    # r/leagueoflegends 의 최신 글 10개를 JSON 포맷으로 합법적으로 요청합니다.
    url = "https://www.reddit.com/r/leagueoflegends/new.json?limit=10"
    
    try:
        async with bot.session.get(url) as resp:
            if resp.status != 200:
                log(f"레딧 연결 실패 (상태 코드: {resp.status})")
                return
            
            data = await resp.json()
            posts = data.get("data", {}).get("children",[])
            # 과거 글부터 올리기 위해 뒤집습니다
            posts.reverse()

            channel = await bot.fetch_channel(REDDIT_NOTI_CHANNEL_ID)
            posted_links = await get_recent_posted_links(channel, limit=100)
            
            log(f"레딧에서 {len(posts)}개의 게시물을 성공적으로 스캔했습니다.")

            for post in posts:
                post_data = post.get("data", {})
                
                # 레딧 글 고유 링크 (https://www.reddit.com/r/... 형식)
                permalink = post_data.get("permalink", "")
                link = f"https://www.reddit.com{permalink}"
                
                # 중복 방지
                if link in posted_links: continue
                
                # 제목과 작성자 추출
                title = html.unescape(post_data.get("title", ""))
                author = post_data.get("author", "Unknown")
                
                # 본문(selftext) 추출. 없으면(링크나 이미지 전용 글인 경우) 빈 문자열 유지
                desc = html.unescape(post_data.get("selftext", ""))
                
                # 본문이 너무 길면 100자로 컷 
                if len(desc) > 100:
                    desc = desc[:100] + "..."
                
                # 첨부 이미지 추출 (게시물이 사진/움짤인지 확인)
                img_url = ""
                # preview 안에 원본 혹은 해상도 조절된 이미지가 존재합니다
                images = post_data.get("preview", {}).get("images",[])
                if images:
                    # url 안의 &amp; 를 & 로 고쳐줘야 이미지가 제대로 뜹니다 
                    img_url = html.unescape(images[0].get("source", {}).get("url", ""))

                # 작성 시간 포맷 (UTC 기준으로 넘겨주므로 변환해야 함)
                created_utc = post_data.get("created_utc")
                date_text = "Reddit"
                if created_utc:
                    dt = datetime.datetime.fromtimestamp(created_utc, tz=timezone.utc)
                    dt_korea = dt.astimezone(KST)
                    date_text = f"{dt_korea.strftime('%Y년 %m월 %d일 %H:%M')}"

                # 레딧 오렌지 테마 컬러 (0xFF4500)
                embed = discord.Embed(title=title, url=link, description=desc, color=0xFF4500)
                
                # 만약 글에 이미지가 있다면 첨부
                if img_url:
                    embed.set_image(url=img_url)
                
                embed.set_footer(text=date_text)
                
                try:
                    await channel.send(embed=embed)
                    log(f"레딧 포스팅 완료: {title[:20]}...")
                    posted_links.append(link)
                except Exception as send_e:
                    log(f"레딧 전송 에러: {send_e}")

    except Exception as e:
        log(f"레딧 파싱 중 에러 발생: {e}")
        traceback.print_exc()

# =================[ 자동 루프 & 이벤트 ] =================
@tasks.loop(minutes=60)
async def main_loop():
    # 3가지 플랫폼 (뉴스, 유튜브, 레딧) 스위치 온!
    await fetch_and_post_news()
    await fetch_and_post_youtube()
    await fetch_and_post_reddit()

@tasks.loop(time=SCHEDULED_VOTE_TIME)
async def daily_vote_loop():
    try:
        channel = await bot.fetch_channel(VOTE_CHANNEL_ID)
        date_str = datetime.datetime.now(KST).strftime("%Y년 %m월 %d일")
        poll = discord.Poll(question=f"🎮 {date_str} 오늘 게임하실 건가요?", duration=timedelta(hours=11))
        for t, e in[("TOP", "🛡️"), ("JGL", "⚔️"), ("MID", "🔥"), ("SUP", "✨"), ("ADC", "🏹"), ("미정", "❓"), ("불참", "❌")]:
            poll.add_answer(text=t, emoji=e)
        await channel.send(poll=poll)
        log(f"투표 게시 완료 ({date_str})")
    except Exception as e: log(f"투표 에러: {e}")

@bot.event
async def on_ready():
    log(f"봇 로그인: {bot.user.name}")
    if not main_loop.is_running(): main_loop.start()
    if not daily_vote_loop.is_running(): daily_vote_loop.start()

# ================= [ 명령어 구역 ] =================
@bot.command()
async def 인증(ctx, *, name=None):
    if not name or "#" not in name:
        return await ctx.send("❌ 사용법: `!인증 소환사명#태그`")
    
    icon_id = random.randint(0, 28)
    pending_users[ctx.author.id] = {"name": name, "icon": icon_id}
    url = f"https://ddragon.leagueoflegends.com/cdn/14.1.1/img/profileicon/{icon_id}.png"
    
    embed = discord.Embed(title="🛡️ 계정 인증", description=f"**{name}**님, 아이콘을 변경 후 `!확인`을 입력하세요.", color=0x5865F2)
    embed.set_thumbnail(url=url)
    await ctx.send(embed=embed)

@bot.command()
async def 확인(ctx):
    user = pending_users.get(ctx.author.id)
    if not user: return await ctx.send("❌ 먼저 `!인증`을 해주세요.")
    
    puuid = await get_puuid(user["name"])
    if not puuid: return await ctx.send("❌ 계정을 찾을 수 없습니다.")

    async with bot.session.get(f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}") as r:
        if r.status == 200 and (await r.json()).get('profileIconId') == user["icon"]:
            await ctx.send(f"✅ 인증 성공! 이제 `!갱신 {user['name']}`을 입력하세요.")
            del pending_users[ctx.author.id]
        else:
            await ctx.send("❌ 아이콘이 일치하지 않습니다.")

@bot.command()
async def 갱신(ctx, *, name=None):
    if not name or "#" not in name: return await ctx.send("❌ 사용법: `!갱신 소환사명#태그`")
    
    puuid = await get_puuid(name)
    if not puuid: return await ctx.send("❌ 계정을 찾을 수 없습니다.")

    async with bot.session.get(f"https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={RIOT_API_KEY}") as r:
        if r.status == 200:
            data = await r.json()
            tier = next((e['tier'] for e in data if e['queueType'] == 'RANKED_SOLO_5x5'), "Unranked").capitalize()
            role = discord.utils.get(ctx.guild.roles, name=tier)
            
            if not role: return await ctx.send(f"❌ '{tier}' 역할이 서버에 없습니다.")
            
            await ctx.author.remove_roles(*[r for r in ctx.author.roles if r.name in TIER_LIST])
            await ctx.author.add_roles(role)
            await ctx.send(f"🔄 **{ctx.author.display_name}**님, **{tier}** 갱신 완료!")

bot.run(DISCORD_TOKEN)
