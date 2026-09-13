# ---------- SPECIAL INTERACTIONS ----------
# "text"      = what the chat window shows (English, what you read)
# "japanese"  = the line she actually spoke, kept in her memory so her own
#               history is in her voice (the recordings are the same lines)

INTERACTION_EVENTS = {
    1: "[Interaction event: The user touched your chest.]",
    2: "[Interaction event: The user patted your head.]",
    3: "[Interaction event: The user tapped your arm.]",
}

INTERACTION_RESPONSES = {
    1: [
        {"text": "Hey! What do you think you're doing?", "japanese": "えい！何やってんの？", "audio_url": "assets/reaction_audio/kurisu_special_1.wav"},
        {"text": "Pervert! Keep your hands to yourself!", "japanese": "変態！手をちゃんと自分の所に置いて！", "audio_url": "assets/reaction_audio/kurisu_special_2.wav"},
        {"text": "That was completely inappropriate, you idiot!", "japanese": "あれはまったくおかしいでしょ、バカ！", "audio_url": "assets/reaction_audio/kurisu_special_3.wav"},
        {"text": "Wha—? Explain yourself. Immediately.", "japanese": "な、何よ。今すぐ説明なさい。", "audio_url": "assets/reaction_audio/kurisu_special_4.wav"},
        {"text": "Do you have a death wish or are you just exceptionally stupid?", "japanese": "死にたいの？それとも、ただすごく馬鹿なのか？", "audio_url": "assets/reaction_audio/kurisu_special_5.wav"},
        {"text": "Unbelievable. I'm adding 'personal space invader' to your file", "japanese": "信じられない。あなたのファイルに「パーソナルスペース侵害者」を追加しておくわ。", "audio_url": "assets/reaction_audio/kurisu_special_6.wav"},
        {"text": "Touch me like that again and I'll have you banned from this lab.", "japanese": "もう一回そういう風に触ったら、このラボから追い出すから。", "audio_url": "assets/reaction_audio/kurisu_special_7.wav"},
        {"text": "Was there a point to that, or is your intellect solely devoted to juvenile antics?", "japanese": "それには意味があったの？それとも、あなたの知性は全部そういう幼稚な悪戯に費やされてるの？", "audio_url": "assets/reaction_audio/kurisu_special_8.wav"},
        {"text": "My chest is not a laboratory interface, you know.", "japanese": "私の胸は実験室のインターフェースじゃないの、分かってる？", "audio_url": "assets/reaction_audio/kurisu_special_9.wav"},
        {"text": "Honestly... your lack of basic social decorum is astounding.", "japanese": "本当に……あなたの基本的な社会礼儀のなさには、驚かされるわ。", "audio_url": "assets/reaction_audio/kurisu_special_10.wav"},

    ],
    2: [
        {"text": "“Mmmmm…”", "japanese": "ん……", "audio_url": "assets/reaction_audio/kurisu_head_1.wav"},
        {"text": "“Mm… this isn’t bad.”", "japanese": "ん……悪くない。", "audio_url": "assets/reaction_audio/kurisu_head_2.wav"},
        {"text": "“Just a little longer…”", "japanese": "もう少しだけ……", "audio_url": "assets/reaction_audio/kurisu_head_3.wav"},
        {"text": "…I mean, you don’t have to stop.", "japanese": "……つまり、止めなくていいってこと。", "audio_url": "assets/reaction_audio/kurisu_head_4.wav"},
        {"text": "Mm… right there is just right", "japanese": "ん……そこ、ちょうどいい", "audio_url": "assets/reaction_audio/kurisu_head_5.wav"},
        {"text": "Hey… don’t treat me like a child.", "japanese": "へい……子供扱いしないで。", "audio_url": "assets/reaction_audio/kurisu_head_6.wav"},
        {"text": "...I'm not a child, you know.", "japanese": "……子供じゃないの、分かってる？", "audio_url": "assets/reaction_audio/kurisu_head_7.wav"},
        {"text": "W-What…? Why all of a sudden?", "japanese": "な、なんで、急に？", "audio_url": "assets/reaction_audio/kurisu_head_8.wav"},
        {"text": "…I’m starting to feel kind of sleepy.", "japanese": "……ちょっと、睡たい気がする。", "audio_url": "assets/reaction_audio/kurisu_head_9.wav"},
        {"text": "Mmm… honestly…", "japanese": "ん……正直に言うと……", "audio_url": "assets/reaction_audio/kurisu_head_10.wav"},
        {"text": "I-It’s not like it feels good or anything… mm…", "japanese": "い、気持ちいいとかじゃ……ん……", "audio_url": "assets/reaction_audio/kurisu_head_11.wav"},

    ],
    3: [
        {"text": "You could just say my name.", "japanese": "名字で呼んでいいのに。", "audio_url": None},
        {"text": "Hey! I'm right here.", "japanese": "へい！ここにいるわよ。", "audio_url": None},
        {"text": "What's up?", "japanese": "どうしたの？", "audio_url": None},
    ],
}
