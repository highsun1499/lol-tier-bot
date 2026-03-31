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

# =================[ 핵심 기능 3: X (트위터) Syndication API 우회 스크래핑 ] =================
async def fetch_and_post_x():
    log("X(트위터) Syndication V2 백도어 우회 시도 중...")
    
    # ★ 제미니 최종 병기: X(트위터) 공식 임베드 위젯(Widget) 전용 백도어 API
    # 이 API는 외부 사이트(블로그, 뉴스 기사)에 트위터를 띄워주기 위해 X 본사에서 '강제로' 열어둔 합법적 우회로입니다.
    # IP 밴을 당하지 않고, 자바스크립트나 보안 토큰 없이 바로 순수 JSON 데이터를 넘겨줍니다!
    syndication_url = "https://cdn.syndication.twimg.com/tweet-result?features=tfw_timeline_list%3A%3Btfw_follower_count_sunset%3Atrue%3Btfw_tweet_edit_backend%3Aon%3Btfw_refsrc_session%3Aon%3Btfw_fosnr_api_calling_resource_backend%3Aon%3Btfw_mixed_media_15897%3Atreatment%3Btfw_experiments_cookie_expiration%3A1209600%3Btfw_show_birdwatch_pivots_enabled%3Aon%3Btfw_duplicate_scribes_to_settings%3Aon%3Btfw_use_profile_image_shape_enabled%3Aon%3Btfw_video_hls_dynamic_manifests_15082%3Atrue_bitrate%3Btfw_legacy_timeline_sunset%3Atrue%3Btfw_tweet_edit_frontend%3Aon&id=LeagueOfLegendsKR&lang=ko"
    
    # 타임라인을 긁어오기 위한 Syndication Timeline API 
    timeline_url = "https://syndication.twitter.com/srv/timeline-profile/screen-name/LeagueOfLegendsKR"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    try:
        # Syndication Timeline 우회 접속 시도
        async with bot.session.get(timeline_url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                log(f"X(트위터) Syndication API 연결 실패: 상태 코드 {resp.status}")
                return
            
            raw_html = await resp.text()
            
            # HTML 깊숙한 곳에 숨겨진 NEXT_DATA JSON 덩어리 추출 (롤 홈페이지 방식과 100% 동일)
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', raw_html)
            if not match:
                log("X(트위터) Syndication 구조 변경됨 - JSON 캡처 실패")
                return

            # JSON 파싱
            twitter_data = json.loads(match.group(1))
            
            # 타임라인 트윗 목록 추출
            try:
                # 딕셔너리 내부를 파고들어서 트윗 데이터를 끌어옵니다
                timeline_entries = twitter_data["props"]["pageProps"]["timeline"]["entries"]
            except KeyError:
                log("X(트위터) 트윗 목록 파싱 에러 - 데이터 구조 변경 의심")
                return
            
            log(f"🔥 X(트위터) 방어벽 돌파 성공! (Syndication API)")
            
            # 트윗 정리 (일반 트윗만 골라냅니다)
            tweets = []
            for entry in timeline_entries:
                if entry["type"] == "tweet":
                    tweets.append(entry["tweet"])
            
            # 최신 10개만 남기고 과거 -> 최신순 정렬
            tweets = tweets[:10]
            tweets.reverse()

            channel = await bot.fetch_channel(X_NOTI_CHANNEL_ID)
            posted_links = await get_recent_posted_links(channel, limit=100)

            for tweet in tweets:
                tweet_id = tweet.get("id_str", "")
                if not tweet_id: continue
                
                link = f"https://x.com/LeagueOfLegendsKR/status/{tweet_id}"
                
                # 답글(Reply)이나 리트윗(RT) 등 무시
                if link in posted_links: continue
                
                # 텍스트 내용 가져오기
                raw_text = tweet.get("text", "")
                if not raw_text: continue
                
                # 텍스트 내의 불필요한 링크(t.co/...) 제거
                clean_desc = re.sub(r'https://t\.co/[a-zA-Z0-9]+', '', raw_text).strip()
                clean_desc = html.unescape(clean_desc)
                
                # 이미지 추출 (media 객체 안에 있는 가장 첫 번째 원본 이미지)
                img_url = ""
                photos = tweet.get("entities", {}).get("media",[])
                if photos and len(photos) > 0:
                    base_url = photos[0].get("media_url_https", "")
                    if base_url:
                        # 트위터 고화질(large) 포맷으로 변환
                        img_url = base_url + "?name=large" if "?" not in base_url else base_url.replace("=normal", "=large")

                # 시간 변환
                date_text = "X(트위터)"
                created_at = tweet.get("created_at", "")
                if created_at:
                    try:
                        # 트위터 자체 시간 포맷 파싱
                        dt = datetime.datetime.strptime(created_at, "%a %b %d %H:%M:%S +0000 %Y")
                        dt = dt.replace(tzinfo=timezone.utc)
                        dt_korea = dt.astimezone(KST)
                        date_text = f"{dt_korea.strftime('%Y년 %m월 %d일 %H:%M')}"
                    except:
                        pass
                
                title = "새로운 트윗"
                if len(clean_desc) > 100:
                    clean_desc = clean_desc[:100] + "..."

                # 디스코드 임베드 제작
                embed = discord.Embed(title=title, url=link, description=clean_desc, color=0xffffff)
                if img_url:
                    embed.set_image(url=img_url)
                
                embed.set_footer(text=date_text)
                
                try:
                    await channel.send(embed=embed)
                    log(f"X 트위터 포스팅 완료: {link}")
                    posted_links.append(link)
                except Exception as send_e:
                    log(f"X 트위터 전송 에러: {send_e}")

    except Exception as e:
        log(f"X(트위터) 최종 크롤링 중 에러 발생: {e}")
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
