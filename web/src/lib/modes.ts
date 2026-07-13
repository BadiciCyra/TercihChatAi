// Gateway'in desteklediği 4 mod + otomatik (NER) yönlendirme.
// mode alanı gateway'e gönderilir; null → gateway soruyu kendisi sınıflandırır.

export type ModeId = "wizard" | "research" | "career" | "guidance";

export interface Mode {
  /** Gateway'e gönderilen değer. null → otomatik (mode alanı gönderilmez). */
  id: ModeId | null;
  emoji: string;
  /** Uzun etiket (mod seçici kartları). */
  label: string;
  /** Kısa etiket (sohbet sırasında kompakt hap). */
  short: string;
  desc: string;
  /** Boş sohbette gösterilen örnek sorular. */
  examples: string[];
}

export const MODES: Mode[] = [
  {
    id: "wizard",
    emoji: "🎯",
    label: "Tercih Sihirbazı",
    short: "Sihirbaz",
    desc: "Sıralamana ve tercihlerine göre sana en uygun üniversite ve bölümleri bulur.",
    examples: [
      "50 bin SAY sıralamasıyla bilgisayar mühendisliği nereye yeter?",
      "260 bin EA ile İstanbul'da hangi hukuk fakülteleri gelir?",
    ],
  },
  {
    id: "research",
    emoji: "🔍",
    label: "Araştırma Asistanı",
    short: "Araştırma",
    desc: "Bir bölüm ya da üniversite hakkında derinlemesine, güncel bilgi toplar.",
    examples: [
      "ODTÜ Bilgisayar Mühendisliği müfredatı ve kariyer olanakları nasıl?",
      "Boğaziçi'nde öğrenci kulüpleri ve kampüs hayatı hakkında bilgi ver.",
    ],
  },
  {
    id: "career",
    emoji: "🧭",
    label: "Kariyer Pusulası",
    short: "Kariyer",
    desc: "İlgi alanların ve hedeflerine göre sana uygun kariyer ve bölümleri önerir.",
    examples: [
      "Yapay zekâ alanında çalışmak istiyorum, hangi bölümü seçmeliyim?",
      "Hem yaratıcı hem iş garantili bölümler için ne önerirsin?",
    ],
  },
  {
    id: "guidance",
    emoji: "💬",
    label: "Rehberlik",
    short: "Rehberlik",
    desc: "Tercih sürecinde motivasyon, strateji ve rehberlik desteği verir.",
    examples: [
      "Tercih listemi yaparken en çok neye dikkat etmeliyim?",
      "İstediğim sıralamayı tutturamadım, şimdi ne yapmalıyım?",
    ],
  },
];

/** Otomatik mod (mode alanı gönderilmez → gateway NER ile yönlendirir). */
export const AUTO_MODE: Mode = {
  id: null,
  emoji: "⚙️",
  label: "Otomatik",
  short: "Otomatik",
  desc: "Sorunu anlayıp senin için en uygun modu kendisi seçer.",
  examples: [
    "Sağlık alanında hangi bölümler var ve hangileri daha çok kazandırıyor?",
    "Bahçeşehir Üniversitesi'nde yazılım mühendisliği okumak mantıklı mı?",
  ],
};

export const ALL_MODES: Mode[] = [...MODES, AUTO_MODE];
