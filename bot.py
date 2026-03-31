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
import xml.etree.ElementTree as ET # ★ X(트위터) RSS 파싱을 위해 추가

# ================= [ 설정 구역 ] =================
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# 아래 4개 채널 ID를 본인 서버에 맞게 설정해 주세요!
NEWS_CHANNEL_ID = 1480944831600656384  
VOTE_CHANNEL_ID = 1484797598241128598  
YT_NOTI_CHANNEL_ID = 1487481812874825879
X_NOTI_CHANNEL_ID = 1487488462536966377

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

# ================= [ 봇 클래스 정의 ] =================
class LoLBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True 
        super().__init__(command_prefix='!', intents=intents)
        self.session = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
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
    try:
        async with bot.session.get(url) as resp:
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

                embed = discord.Embed(title=title, url=link, description=desc[:100], color=0xc28f2c)
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
        log("유튜브 API 키가 환경변수에 없습니다.")
        return
        
    url = f"https://www.googleapis.com/youtube/v3/playlistItems?key={YOUTUBE_API_KEY}&playlistId={UPLOADS_PLAYLIST_ID}&part=snippet&maxResults=10"
    try:
        async with bot.session.get(url) as resp:
            if resp.status != 200: 
                error_msg = await resp.text()
                log(f"유튜브 API 호출 실패 ({resp.status}) / {error_msg}")
                return
            
            data = await resp.json()
            videos = data.get('items',[])
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
                
                try:
                    await channel.send(embed=embed)
                    log(f"유튜브 포스팅 완료: {title}")
                    posted_links.append(v_url)
                except Exception as send_e:
                    log(f"유튜브 전송 실패: {title} - {send_e}")
                
    except Exception as e: 
        log(f"유튜브 에러: {e}")
        traceback.print_exc()

# ================= [ 핵심 기능 3: X (트위터) RSS 수집 ] =================
async def fetch_and_post_x():
    log("X(트위터) 게시물 체크 중...")
    
    # ★ 수정됨: X(트위터) 우회를 위한 전 세계 Nitter/RSSHub 미러 서버 백업 리스트를 대폭 늘렸습니다.
    # 위에서부터 차례대로 접속을 시도하고 하나라도 뚫리면 바로 가져옵니다.
    rss_urls =[
        "https://rsshub.app/twitter/user/LeagueOfLegendsKR", 
        "https://nitter.poast.org/LeagueOfLegendsKR/rss",
        "https://nitter.cz/LeagueOfLegendsKR/rss",
        "https://nitter.privacydev.net/LeagueOfLegendsKR/rss",
        "https://nitter.projectsegfau.lt/LeagueOfLegendsKR/rss",
        "https://mstdn.social/users/LeagueOfLegendsKR.rss" # 완전 막힐 경우를 대비한 대체 라우팅
    ]
    
    success = False
    xml_data = ""
    
    # 여러 서버 중 연결되고 내용이 있는 첫 번째 서버를 사용합니다.
    # timeout을 10초에서 5초로 줄여서, 죽은 서버를 빠르게 손절하고 다음 서버로 넘어가게 최적화했습니다.
    for url in rss_urls:
        try:
            async with bot.session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    xml_data = await resp.text()
                    if "<item>" in xml_data: # 정상적인 게시물 데이터가 들어있는지 확인
                        success = True
                        break
        except:
            continue # 연결 실패(Timeout) 시 다음 서버 주소로 넘어감
            
    if not success:
        log("X(트위터) 무료 RSS 우회 서버들이 현재 모두 막혀있습니다. 1시간 뒤에 다시 시도합니다.")
        return
        
    try:
        root = ET.fromstring(xml_data)
        channel_node = root.find("channel")
        items = channel_node.findall("item")[:10] # 최신 10개만
        items.reverse() # 과거 -> 최신
        
        channel = await bot.fetch_channel(X_NOTI_CHANNEL_ID)
        posted_links = await get_recent_posted_links(channel, limit=100)
        
        for item in items:
            link = item.findtext("link", "").strip()
            
            # 리트윗(RT)이나 답글은 보통 무시하지만 가져오고 싶다면 조건문 제거
            if link in posted_links: continue
            
            # 원시 내용(HTML 태그 포함됨)
            raw_desc = item.findtext("description", "")
            
            # 사진 찾기 (RSSHub는 보통 img src로 이미지를 넣어줌)
            img_url = ""
            img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_desc)
            if img_match:
                img_url = img_match.group(1)
            else:
                # RSS 표준 첨부파일 방식 확인
                enclosure = item.find("enclosure")
                if enclosure is not None and enclosure.get("url"):
                    img_url = enclosure.get("url")

            # 줄바꿈 태그(<br>)를 실제 줄바꿈으로 변환
            clean_desc = re.sub(r'<br\s*/?>', '\n', raw_desc)
            # 나머지 모든 HTML 태그 제거
            clean_desc = re.sub(r'<[^>]+>', '', clean_desc)
            # 특수기호 복원
            clean_desc = html.unescape(clean_desc).strip()
            
            # 작성일 포맷팅 (형식: "Mon, 30 Mar 2026 12:00:00 GMT")
            pub_date_str = item.findtext("pubDate", "")
            date_text = "X(트위터)"
            if pub_date_str:
                try:
                    dt = datetime.datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z")
                    dt = dt.replace(tzinfo=timezone.utc)
                    dt_korea = dt.astimezone(KST)
                    date_text = f"{dt_korea.strftime('%Y년 %m월 %d일 %H:%M')}"
                except:
                    pass

            title = item.findtext("title", "새로운 트윗").strip()
            
            # 내용을 100글자 정도로 제한하여 보여줌 (가독성 목적)
            if len(clean_desc) > 100:
                clean_desc = clean_desc[:100] + "..."

            embed = discord.Embed(title=title, url=link, description=clean_desc, color=0xFFFFFF)
            if img_url:
                # 트위터 이미지 서버(pbs.twimg.com) 크기 원본으로 맞추기 (옵션)
                if "?format=" in img_url:
                    img_url = re.sub(r'&name=[a-zA-Z0-9]+', '&name=large', img_url)
                embed.set_image(url=img_url)
            
            embed.set_footer(text=date_text)
            
            try:
                await channel.send(embed=embed)
                log(f"X 트위터 포스팅 완료: {link}")
                posted_links.append(link)
            except Exception as send_e:
                log(f"X 트위터 전송 실패: {send_e}")

    except Exception as e:
        log(f"X 트위터 파싱 에러: {e}")
        traceback.print_exc()

# ================= [ 자동 루프 & 이벤트 ] =================
@tasks.loop(minutes=60)
async def main_loop():
    # 3가지 플랫폼을 1시간마다 확인
    await fetch_and_post_news()
    await fetch_and_post_youtube()
    await fetch_and_post_x()

@tasks.loop(time=SCHEDULED_VOTE_TIME)
async def daily_vote_loop():
    try:
        channel = await bot.fetch_channel(VOTE_CHANNEL_ID)
        date_str = datetime.datetime.now(KST).strftime("%Y년 %m월 %d일")
        poll = discord.Poll(question=f"🎮 {date_str} 오늘 게임하실 건가요? (포지션 선택)", duration=timedelta(hours=11))
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
