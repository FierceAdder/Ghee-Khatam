"""Words the generator builds records from, split into training and test ingredients.

Single name words, place-name parts and labels only: full names are random combinations
made at run time and never written here. The split is fixed: test ingredients (name
regions, label wordings, forms of address, village endings, layouts) are never used while
finding and fixing failures, so a result on them says something about wording we did not
tune on. Sources: general knowledge of Indian names, places and hospital forms; see
docs/TRAPS.md.
"""

from __future__ import annotations

# ----------------------------------------------------------------- names
# region -> (female given, male given, surnames, forms of address, how they attach)
# Each word is "Latin:Devanagari". Forms of address: (word, "before" | "after").

REGIONS = {
    "hindi_belt": dict(
        female="Leela:लीला Kamla:कमला Savitri:सावित्री Pushpa:पुष्पा Urmila:उर्मिला "
               "Saroj:सरोज Kusum:कुसुम Shanno:शन्नो Bimla:बिमला Rajkumari:राजकुमारी",
        male="Ramu:रामू Brijmohan:बृजमोहन Shivnath:शिवनाथ Ramkishan:रामकिशन Kailash:कैलाश "
             "Harinarayan:हरिनारायण Chhotelal:छोटेलाल Bhagwandin:भगवानदीन Jagdev:जगदेव "
             "Rambharose:रामभरोसे",
        surnames="Yadav:यादव Maurya:मौर्य Kushwaha:कुशवाहा Pal:पाल Rawat:रावत Lodhi:लोधी "
                 "Gautam:गौतम Kanaujia:कनौजिया Prajapati:प्रजापति Rajbhar:राजभर",
        address=[("ji", "after"), ("sahab", "after"), ("bhai", "after"), ("amma", "after"),
                 ("didi", "after"), ("bhaiya", "after")]),
    "punjabi": dict(
        female="Harpreet:हरप्रीत Gurmeet:गुरमीत Manjit:मनजीत Rajwinder:राजविंदर "
               "Sukhjit:सुखजीत Paramjit:परमजीत Navneet:नवनीत Amandeep:अमनदीप",
        male="Baldev:बलदेव Jasbir:जसबीर Gurdeep:गुरदीप Kulwant:कुलवंत Hardev:हरदेव "
             "Balwinder:बलविंदर Satnam:सतनाम Karamjit:करमजीत Joginder:जोगिंदर",
        surnames="Gill:गिल Sandhu:संधू Dhillon:ढिल्लों Brar:बराड़ Sidhu:सिद्धू",
        address=[("ji", "after"), ("paaji", "after"), ("bibi", "after")]),
    "muslim": dict(
        female="Rukhsana:रुख़साना Shabnam:शबनम Nazia:नाज़िया Farzana:फ़रज़ाना "
               "Shaheen:शाहीन Tabassum:तबस्सुम Afsana:अफ़साना Nargis:नरगिस",
        male="Imran:इमरान Saleem:सलीम Arshad:अरशद Rizwan:रिज़वान Naushad:नौशाद "
             "Shamim:शमीम Irfan:इरफ़ान Wasim:वसीम Javed:जावेद",
        surnames="Ansari:अंसारी Qureshi:क़ुरैशी Siddiqui:सिद्दीक़ी Shaikh:शेख़ Mansuri:मंसूरी",
        address=[("sahab", "after"), ("begum", "after"), ("khala", "after")]),
    "bengali": dict(
        female="Moumita:मौमिता Sharmila:शर्मिला Rupa:रूपा Tanushree:तनुश्री "
               "Sumana:सुमना Rina:रीना Debjani:देबजानी Sutapa:सुतपा",
        male="Haren:हरेन Subrata:सुब्रत Tapan:तपन Biswajit:बिस्वजीत Prasenjit:प्रसेनजीत "
             "Anirban:अनिर्बान Dipankar:दीपंकर Sujoy:सुजय",
        surnames="Ghosh:घोष Mondal:मंडल Sarkar:सरकार Bhattacharya:भट्टाचार्य Halder:हलदर",
        address=[("da", "after"), ("di", "after"), ("babu", "after")]),
    # ---- test regions: never used while fixing
    "northeast": dict(
        female="Lalremsiami:लालरेमसियामी Zothansangi:ज़ोथनसांगी Ibashisha:इबाशिशा "
               "Thoibi:थोइबी Sentirenla:सेंतिरेनला",
        male="Lalthanzuala:ललथनज़ुआला Vanlalruata:वनलालरुआता Kyrshan:किरशान Tomba:तोम्बा "
             "Imtisunep:इम्तिसुनेप",
        surnames="Ralte:राल्ते Hmar:ह्मार Pachuau:पचुआउ Syiem:स्यिएम Lyngdoh:लिंगदोह "
                 "Ao:आओ Longchar:लोंगचार",
        address=[("Pu", "before"), ("Pi", "before"), ("Bah", "before"), ("Kong", "before")]),
    "south": dict(
        female="Selvi:सेल्वी Muthulakshmi:मुथुलक्ष्मी Padmavathi:पद्मावती Revathi:रेवती",
        male="Murugan:मुरुगन Arumugam:अरुमुगम Venkatesh:वेंकटेश Srinivasulu:श्रीनिवासुलु",
        surnames="Reddy:रेड्डी Naidu:नायडू Pillai:पिल्लई Gounder:गौंडर Nair:नायर",
        address=[("garu", "after"), ("anna", "after"), ("akka", "after"),
                 ("Thiru", "before"), ("Thirumathi", "before")]),
    "adivasi": dict(
        female="Sukhmuni:सुखमुनी Phulmani:फूलमनी Salo:सालो Jhingi:झिंगी",
        male="Budhu:बुधु Somra:सोमरा Etwa:एतवा Mangra:मंगरा",
        surnames="Oraon:उरांव Munda:मुंडा Kujur:कुजूर Tirkey:तिर्की Hembrom:हेम्ब्रम Lakra:लकड़ा",
        address=[("didi", "after"), ("dada", "after")]),
    "west": dict(
        female="Sunanda:सुनंदा Vaishali:वैशाली Hansa:हंसा Jyotsna:ज्योत्स्ना",
        male="Dattatray:दत्तात्रय Bhikhu:भीखू Tukaram:तुकाराम Navin:नवीन",
        surnames="Patil:पाटिल Jadhav:जाधव Kale:काले Solanki:सोलंकी Rathwa:राठवा",
        address=[("bhai", "joined"), ("ben", "joined"), ("tai", "after"), ("rao", "after")]),
}

TRAIN_REGIONS = ("hindi_belt", "punjabi", "muslim", "bengali")
TEST_REGIONS = ("northeast", "south", "adivasi", "west")

# Doctors and staff: given names deliberately overlap relatives' names (trap N-01).
DOCTOR_SURNAMES = "Saxena:सक्सेना Bajpai:बाजपेयी Ekka:एक्का Menon:मेनन Bhatia:भाटिया Dutta:दत्ता"

# ----------------------------------------------------------------- places

VILLAGE_ROOTS = {"train": "Ram:राम Shiv:शिव Kishan:किशन Hari:हरि Gopal:गोपाल Sita:सीता",
                 "test": "Bhim:भीम Chand:चांद Sohan:सोहन Durga:दुर्गा Lakhan:लखन Moti:मोती"}
VILLAGE_ENDINGS = {"train": "pur:पुर ganj:गंज nagar:नगर",
                   "test": "garh:गढ़ khera:खेड़ा purwa:पुरवा dih:डीह palli:पल्ली wadi:वाड़ी"}
TWO_WORD_VILLAGE = {"train": "Khurd:खुर्द Kalan:कलां", "test": "Buzurg:बुज़ुर्ग"}
DISTRICTS = {"train": [("Sitapur", "सीतापुर", "261"), ("Barabanki", "बाराबंकी", "225"),
                       ("Unnao", "उन्नाव", "209"), ("Hardoi", "हरदोई", "241")],
             "test": [("Ranchi", "रांची", "834"), ("Gumla", "गुमला", "835"),
                      ("Bahraich", "बहराइच", "271"), ("Gonda", "गोंडा", "271")]}
LANDMARKS = "Hanuman Mandir:हनुमान मंदिर|Panchayat Bhawan:पंचायत भवन|Pani Tanki:पानी टंकी|" \
            "Primary School:प्राथमिक विद्यालय|Jama Masjid:जामा मस्जिद|Bada Talab:बड़ा तालाब"

# ----------------------------------------------------------------- labels
# field -> (train wordings, test wordings). Test wordings are absent from the practice data.

LABELS = {
    "name": (["Patient Name", "Name", "Pt Name", "Naam", "नाम", "मरीज़ का नाम"],
             ["Rogi", "Card Holder", "Person", "Client Name", "रुग्ण", "लाभार्थी का नाम"]),
    "id": (["UHID", "Reg No", "MRN", "IPD No", "पंजीकरण सं."],
           ["Card No", "OPD Ticket", "CR No", "Folio", "पर्ची सं."]),
    "phone": (["Mobile", "Mob", "Phone", "मोबाइल"],
              ["Contact", "WhatsApp", "Cell", "संपर्क सूत्र"]),
    "aadhaar": (["Aadhaar", "Aadhar No"], ["UID", "Govt ID", "पहचान सं."]),
    "age": (["Age", "Age/Sex", "आयु"], ["Yrs", "A/S", "उम्र/लिंग"]),
    "dob": (["DOB", "Date of Birth", "जन्म तिथि"], ["D.O.B.", "Born", "जन्म"]),
    "address": (["Address", "पता"], ["Lives at", "Residence", "निवासी"]),
    "village": (["Village", "Gram", "ग्राम"], ["Native Place", "Mauza", "बस्ती"]),
    "relative": (["Father/Husband Name", "Guardian", "पिता का नाम"],
                 ["Accompanied by", "Next of kin", "संरक्षक"]),
    "date": (["Date", "Date of Admission", "तारीख"], ["Seen on", "Dt.", "दिनांक"]),
}
JSON_KEYS = {
    "name": (["patient_name", "name"], ["person", "holder"]),
    "id": (["uhid", "mrn"], ["card", "folio"]),
    "phone": (["mobile", "phone"], ["contact", "whatsapp"]),
    "dob": (["dob"], ["born"]),
    "village": (["village"], ["native_place"]),
}
RELATIONS = {"train": [("S/o", "M"), ("W/o", "F"), ("D/o", "F"), ("पुत्र", "M")],
             "test": [("Son of", "M"), ("Daughter of", "F"), ("Wd/o", "F"), ("वल्द", "M")]}

# ----------------------------------------------------------------- medicine

DIAGNOSES = [("Typhoid fever", "A01.0"), ("Pneumonia, unspecified", "J18.9"),
             ("Essential (primary) hypertension", "I10"),
             ("Type 2 diabetes mellitus without complications", "E11.9"),
             ("Anaemia, unspecified", "D64.9"), ("Dengue fever", "A90"),
             ("Malaria, unspecified", "B54"), ("Tuberculosis of lung", "A15.0"),
             ("Acute gastroenteritis", "A09"), ("Urinary tract infection", "N39.0")]
RARE_DIAGNOSES = [("Scrub typhus", "A75.3"), ("Leptospirosis, unspecified", "A27.9")]
EPONYMS = ["Parkinson's disease", "Addison's disease", "Bell's palsy", "Down syndrome"]
DRUGS = [("Tab Paracetamol", "650 mg", "1-1-1"), ("Tab Amlodipine", "5 mg", "0-0-1"),
         ("Tab Metformin", "500 mg", "1-0-1"), ("Cap Doxycycline", "100 mg", "1-0-1"),
         ("Tab Pantoprazole", "40 mg", "1-0-0"), ("Inj Ceftriaxone", "1 g", "IV BD"),
         ("Tab Azithromycin", "500 mg", "1-0-0"), ("Syp Ondansetron", "2 mg", "½-0-½")]
LAB_TESTS = [("Haemoglobin", "g/dL", (7.0, 15.5), "718-7"),
             ("Platelet count", "/uL", (40000, 410000), "777-3"),
             ("Random Blood Sugar", "mg/dL", (80, 320), "2345-7"),
             ("Serum Creatinine", "mg/dL", (0.5, 3.5), "2160-0")]
COMPLAINTS = ["fever x {n} days", "cough x {n} days", "pain abdomen x {n} days",
              "vomiting x {n} days"]
STAFF_ROLES = ["Staff Nurse", "Sister", "Lab Technician", "Pharmacist", "ANM", "ASHA worker"]


def pairs(spec: str) -> list[tuple[str, str]]:
    return [tuple(item.split(":", 1)) for item in spec.split()]


def landmarks() -> list[tuple[str, str]]:
    return [tuple(item.split(":", 1)) for item in LANDMARKS.split("|")]
