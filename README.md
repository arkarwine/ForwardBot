# ForwardBot

Telegram message link များကို ကူးယူပြီး command ပို့သူ၏ private chat သို့ ရလဒ်ပို့ပေးသော Kurigram + SQLite Telegram bot ဖြစ်ပါသည်။

## Flow

အများမြင် link:

```text
user: /copy https://t.me/gemini12pro/159438
bot: DEFAULT_USER_SESSION_STRING ဖြင့် ဖတ်ပြီး ကူးယူကာ ကူးယူထားသော message ကို DM ပြန်ပို့ပါသည်။
```

Private link:

```text
user: /copy https://t.me/c/123456789/42
bot: ဝင်ရောက်ခွင့်ပေးမည့်နည်းလမ်းကို မေးပါသည်။
```

Private ဝင်ရောက်ခွင့်နည်းလမ်းများ:

- `Invite link သုံးမည်` - user က invite link ပို့ပါသည်။ Bot သည် မူလ session ဖြင့် ဝင်ရောက်ပြီး လင့်ခ်ပါ message ကို ကူးယူပါသည်။
- `Member account ဖြင့် Login` - user သည် Kurigram လမ်းညွှန်ချက်အတိုင်း ပထမအကြိမ် login ဝင်ပါသည်။ Bot သည် session string ကို SQLite database ထဲတွင် သိမ်းထားပြီး နောက်တစ်ကြိမ်များတွင် ထို member account ဖြင့် အလိုအလျောက် ဆက်အသုံးပြုပါသည်။

သီးခြား `/login` command ကို မသုံးနိုင်အောင် ဖယ်ရှားထားပါသည်။ Private link အတွက် member account လိုအပ်သည့်အခါမှသာ Login ရွေးချယ်ခွင့် ပေါ်လာပါမည်။

## တပ်ဆင်ခြင်း

1. `https://my.telegram.org` တွင် Telegram API app တစ်ခု ဖန်တီးပါ။
2. `.env.example` ကို `.env` အဖြစ် ကူးယူပါ။
3. `API_ID`, `API_HASH`, `BOT_TOKEN` နှင့် `DEFAULT_USER_SESSION_STRING` တို့ကို ဖြည့်ပါ။
   `DEFAULT_USER_SESSION_STRING` မရှိပါက bot စတင်မည်မဟုတ်ပါ။
4. လိုအပ်သော package များကို ထည့်သွင်းပါ:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

5. Bot ကို စတင်ပါ:

```powershell
.\.venv\Scripts\python -m forwardbot
```

## Commands များ

- `/start` - အကူအညီပြသရန်။
- `/copy MESSAGE_LINK` - အများမြင် သို့မဟုတ် private Telegram message link ကို ကူးယူရန်။
- `/cancel` - လက်ရှိ private-link လုပ်ငန်းစဉ်ကို ပယ်ဖျက်ရန်။

## မှတ်ချက်များ

- `/copy` သည် ကူးယူထားသော message ကို ပို့သူ၏ private chat သို့ အမြဲပို့ပါသည်။
- Group မှ `/copy` အသုံးပြုပါက bot ကို private chat တွင်ဖွင့်ပြီး Start ကို တစ်ကြိမ်နှိပ်ရပါမည်။ ထိုအခါ bot က DM ပို့နိုင်ပါမည်။
- Bot စတင်ချိန်တွင် `DEFAULT_USER_SESSION_STRING` မဖြစ်မနေ လိုအပ်ပါသည်။
- အများမြင် link များအတွက် `DEFAULT_USER_SESSION_STRING` လိုအပ်ပါသည်။ Bot သည် server CLI တွင် phone number မမေးပါ။
- Private login လုပ်ငန်းစဉ်တွင် Telegram login code ကို chat message အဖြစ် မပို့ဘဲ inline button များဖြင့် ထည့်ရပါသည်။ Telegram ခွင့်ပြုပါက phone နှင့် password message များကို ဖျက်ပေးပါသည်။
- Member account session string များသည် login credential ဖြစ်သောကြောင့် `DB_PATH` ဖြင့် သတ်မှတ်ထားသော SQLite database ကို လုံခြုံစွာ သိမ်းဆည်းပါ။
- Bot နှင့် user session များသည် Telegram ၏ access control ကို ကျော်လွှား၍ မရပါ။ မူလ session သို့မဟုတ် ယာယီ login account သည် မူရင်း message ကို တရားဝင် ဝင်ရောက်ကြည့်ရှုနိုင်ရပါမည်။
