"""The normaliser, against spec strings and titles copied from real Best Buy listings
(collected 19 Nov 2026). Every row documents one way the same spec is written."""
import pytest
from conftest import make_product

from tracker import normalise as N

WIN = "Windows 11 Home"

# ---------------------------------------------------------------------------- CPU
# (processor brand, "Processor Model" series, "Processor Model Number", title, identity, name, class)
CPU_CASES = [
    # the brief's Lenovo: the title has no model number, the spec table does
    ("Intel", "Intel Core Ultra 5 Series 2", "226V",
     'Lenovo - Yoga 7i 2-in-1 - Copilot+ PC - 16" 2K Touchscreen Laptop - Intel Core Ultra 5 Processor 2024 - 16GB Memory - 512GB SSD - Luna Grey',
     "intel-226v", "Intel Core Ultra 5 226V", "intel-core-ultra-5-s2"),
    ("Intel", "Intel Core Ultra 5 Series 2", "226V",
     'HP - OmniBook X Flip 2-in-1 - 14" 2K Touchscreen Laptop - Intel Core Ultra 5 226V (2024) - 16GB Memory - 512GB SSD',
     "intel-226v", "Intel Core Ultra 5 226V", "intel-core-ultra-5-s2"),
    # one CPU, five spellings of the model-number field, a wrong series on one of them
    ("Intel", "Intel Core Ultra 7 Series 3", "355",
     'Lenovo - Yoga 7i 2-in-1 16" 2K OLED Touchscreen Laptop - Intel Core Ultra 7 355 (2026) - 24GB Memory',
     "intel-355", "Intel Core Ultra 7 355", "intel-core-ultra-7-s3"),
    ("Intel", "Intel Core Ultra 7 Series 1", "Ultra 7 355",
     'Lenovo - ThinkPad T14 G7 14" Copilot+ PC - Latest Model - Intel Ultra7 with 32GB Memory - 512GB SSD - Black',
     "intel-355", "Intel Core Ultra 7 355", "intel-core-ultra-7-s3"),
    ("Intel", "Intel Core Ultra 7 Series 3", "U7 355",
     'ASUS - Vivobook S14 14" OLED Laptop - Intel Core Ultra 7 355 CPU - 32GB RAM - 1TB SSD',
     "intel-355", "Intel Core Ultra 7 355", "intel-core-ultra-7-s3"),
    ("Intel", "Intel Core Ultra 7 Series 3", "Processor 355",
     'Lenovo - Yoga 7i 2-in-1 14" 2K OLED Touchscreen Laptop - Core Ultra 7 Processor 355 (2026)- 24GB Memory',
     "intel-355", "Intel Core Ultra 7 355", "intel-core-ultra-7-s3"),
    ("Intel", "Intel Core Ultra X7 Series 3", "X7-358H",
     'ASUS - Zenbook 14" Laptop - Intel Core Ultra X7 358H (2026) - 32GB Memory',
     "intel-358h", "Intel Core Ultra X7 358H", "intel-core-ultra-x7-s3"),
    ("Intel", "Intel Core Ultra 9 Series 3", "Ultra 9-386H",
     'MSI - Stealth 16 AI+ 16" 2.5K 240Hz OLED Gaming Laptop Intel Core U9-386H with 32GB Memory',
     "intel-386h", "Intel Core Ultra 9 386H", "intel-core-ultra-9-s3"),
    # Core (not Ultra): the series field says "Intel Processor 300" for some listings
    ("Intel", "Intel Core 7 Series 3", "IntelCore 7 350",
     'Lenovo - ThinkPad E14 Gen 8 14" AI PC - Latest Model - Intel Core 7 with 16GB Memory - 512GB SSD',
     "intel-350", "Intel Core 7 350", "intel-core-7-s3"),
    ("Intel", "Intel Processor 300", "350",
     'Lenovo - IdeaPad Slim 3i 15.3" 2K Touchscreen Laptop - Core 7 Processor 350 (2026) - 16GB Memory',
     "intel-350", "Intel Core 7 350", "intel-core-7-s3"),
    ("Intel", "Intel Core 5 Series 3", "Core 5  320",
     'Acer - Aspire 16" Laptop - Intel Core 5 Series 3 2026 - 8GB Memory - 512GB Storage',
     "intel-320", "Intel Core 5 320", "intel-core-5-s3"),
    # Core i
    ("Intel", "Intel Core i9 14th Generation", "i9-14900HX",
     'ASUS - ROG Strix G16 16" Gaming Laptop - Intel Core i9 HX - 16GB Memory - GeForce RTX 5070',
     "intel-14900hx", "Intel Core i9-14900HX", "intel-core-i9-g14"),
    ("Intel", "Intel 13th Generation Core i5", "1334U",
     'Dell - 15.6" Laptop - Intel 13th Generation Core i5 1334U 2023 - 8GB Memory - 512GB SSD',
     "intel-1334u", "Intel Core i5-1334U", "intel-core-i5-g13"),
    ("Intel", "Intel 12th Generation Core i5", "Not provided",          # number only in the title
     'Acer - Nitro 5 17.3" Full HD IPS 144Hz Gaming Laptop- Intel Core i5-12500H- NVIDIA GeForce RTX 4050 - 8GB DDR4',
     "intel-12500h", "Intel Core i5-12500H", "intel-core-i5-g12"),
    ("Intel", "Intel Core Ultra 7 Series 2", None,
     'Samsung - Galaxy Book5 15.6” Laptop - Intel Ultra 7 255U with 16GB Memory - 512GB SSD - Silver',
     "intel-255u", "Intel Core Ultra 7 255U", "intel-core-ultra-7-s2"),
    # N-series and Celeron: five different series labels for the N355 alone
    ("Intel", "Intel Core 3 Series 1", "N355",
     'ASUS - Chromebook Plus CX14 - 14" 2K Laptop - Intel Core 3 Series 1 (2025) - 8GB Memory - 128GB Storage',
     "intel-n355", "Intel Processor N355", "intel-n355"),
    ("Intel", "Intel Processor 300", "N355",
     'Lenovo - IdeaPad Slim 3i 15.6" Full HD Laptop - Intel Processor N355 (2025)- 8GB Memory 128GB Storage',
     "intel-n355", "Intel Processor N355", "intel-n355"),
    ("Intel", "Intel Celeron", "N4500",
     'Lenovo - Chromebook IdeaPad 3i - 15.6" 2K Laptop - Intel Celeron (2021) - 4GB Memory - 64GB eMMC Storage',
     "intel-n4500", "Intel Celeron N4500", "intel-n4500"),
    # AMD: the family and tier often live only in the title; the series can be plain wrong
    ("AMD", "AMD Ryzen 7 3000 Series", "350",
     'Lenovo - ThinkPad T14s Gen 6 14" Laptop - AMD Ryzen AI 7 PRO 350 with 32GB Memory - 512GB SSD - Black',
     "amd-350", "AMD Ryzen AI 7 PRO 350", "amd-ryzen-ai-7-300"),
    ("AMD", "AMD Ryzen AI 400 Series", "AI 5 430",
     'Lenovo - Yoga 7a 2-in-1 14" 2K OLED Touchscreen Laptop - AMD Ryzen AI 5 430 (2026)- 16GB Memory',
     "amd-430", "AMD Ryzen AI 5 430", "amd-ryzen-ai-5-400"),
    ("AMD", "AMD Ryzen AI 400 Series", "5 430",
     'HP - OmniBook X Flip 2-in-1 - 14" 2K Touchscreen Laptop - AMD Ryzen AI 5 430 (2026) - 16GB Memory',
     "amd-430", "AMD Ryzen AI 5 430", "amd-ryzen-ai-5-400"),
    ("AMD", "AMD Ryzen AI 5 400 Series", "Ryzen AI 5 430",
     'Dell - 14S - 14" 2K Touchscreen Laptop - AMD Ryzen AI 5 430 2026 - 16GB Memory',
     "amd-430", "AMD Ryzen AI 5 430", "amd-ryzen-ai-5-400"),
    ("AMD", "AMD Ryzen AI 300 Series", "HX 370",
     'ASUS - Zenbook S16 16" 3K OLED Touchscreen Laptop - AMD Ryzen AI 9 HX 370 - 32GB Memory',
     "amd-370", "AMD Ryzen AI 9 HX 370", "amd-ryzen-ai-9-300"),
    ("AMD", "AMD Ryzen AI 400 Series", "AI 9 HX470",
     'ASUS - ROG Zephyrus G14 14" Gaming Laptop - AMD Ryzen AI 9 HX470 - 32GB Memory',
     "amd-470", "AMD Ryzen AI 9 HX 470", "amd-ryzen-ai-9-400"),
    ("AMD", "AMD Ryzen AI 7 300 Series", "Krackan AI 7345",               # "AI 7 345" with the space lost
     'Lenovo - ThinkBook 14 Gen 8 14" Laptop - Latest Model - AMD Ryzen AI 7 with 16GB Memory - 512GB SSD',
     "amd-345", "AMD Ryzen AI 7 345", "amd-ryzen-ai-7-300"),
    ("AMD", "AMD Ryzen AI Max+ 300 Series", "Max+ 388",
     'Lenovo - Yoga Pro 7a 15.3" 3K OLED Touchscreen Laptop - AMD Ryzen AI Max+ 388 (2026) - 32GB RAM',
     "amd-388", "AMD Ryzen AI Max+ 388", "amd-ryzen-ai-max-300"),
    ("AMD", "AMD Ryzen 200 Series", "250",
     'Dell - 15" 2K Touchscreen Laptop - AMD Ryzen 200 Series Ryzen 7 2026- 16GB Memory - 512GB Storage',
     "amd-250", "AMD Ryzen 7 250", "amd-ryzen-7-200"),
    ("AMD", "AMD Ryzen 7 7000 Series", "7730U Octa",
     'Acer - Aspire Go 15 – 15.6" FHD Laptop – AMD Ryzen 7 7730U 2023 – AMD Radeon Graphics – 16GB',
     "amd-7730u", "AMD Ryzen 7 7730U", "amd-ryzen-7-7000"),
    ("AMD", "AMD Ryzen 5 3000 Series", "R5-3501U",
     'ASUS - Vivobook 16 16” 2K Laptop - AMD Ryzen 5 3501U (2019) - 8GB Memory - 256GB Storage',
     "amd-3501u", "AMD Ryzen 5 3501U", "amd-ryzen-5-3000"),
    ("AMD", "AMD 10 Series", "40",
     'Lenovo - IdeaPad Slim 3 15.6" Full HD Laptop - AMD Ryzen 5 40 (2025) - 8GB Memory- 256GB Storage',
     "amd-40", "AMD Ryzen 5 40", "amd-40"),
    # Snapdragon: one model number, four ways of punctuating it
    ("Snapdragon", "Snapdragon X", "X1-26-100",
     'HP - OmniBook 3 - 14" 2K OLED Laptop - Snapdragon X (8-Core CPU) - 8GB Memory - 256GB SSD',
     "qualcomm-x1-26-100", "Snapdragon X X1-26-100", "qualcomm-x1"),
    ("Snapdragon", "Snapdragon X", "X126100",
     'ASUS - Vivobook 14 14" 2K Laptop - Snapdragon X X1 - 16GB Memory',
     "qualcomm-x1-26-100", "Snapdragon X X1-26-100", "qualcomm-x1"),
    ("Snapdragon", "Snapdragon X", "X1 26 100",
     'ASUS - Zenbook A14 14" Laptop - Snapdragon X with 32GB RAM',
     "qualcomm-x1-26-100", "Snapdragon X X1-26-100", "qualcomm-x1"),
    ("Snapdragon", "Snapdragon X2 Elite", "X2E88100",
     'ASUS - Zenbook A16 16" Laptop - Snapdragon X2 Elite 18-core - 32GB Memory',
     "qualcomm-x2e-88-100", "Snapdragon X2 Elite X2E-88-100", "qualcomm-x2e"),
    ("Snapdragon", "Snapdragon X Plus", "X1P-42-100",
     'HP - OmniBook 5 16" Laptop - Snapdragon X Plus (2024) - 16GB Memory',
     "qualcomm-x1p-42-100", "Snapdragon X Plus X1P-42-100", "qualcomm-x1p"),
    # MediaTek, NVIDIA
    ("MediaTek", "MediaTek Kompanio 500 Series", "Kompanio 540",
     'Acer - Chromebook 315 – 15.6" 2K Laptop - MediaTek Kompanio 500 Series 2025 - 4GB Memory - 64GB Storage',
     "mediatek-540", "MediaTek Kompanio 540", "mediatek-540"),
    ("MediaTek", "MediaTek Kompanio 500 Series", "K520",
     'ASUS - Chromebook CM14 Flip - 14" 2K Touchscreen Laptop - Mediatek Kompanio 500 Series 2022 - 8GB Memory',
     "mediatek-520", "MediaTek Kompanio 520", "mediatek-520"),
    ("MediaTek", "MediaTek MT Series", "MT8189GV/AZ",
     'Acer - Chromebook Spin 311 - 11.6" Laptop - MediaTek MT Series (2026) - 4GB Memory',
     "mediatek-mt8189", "MediaTek MT8189", "mediatek-mt8189"),
    ("NVIDIA", "NVIDIA RTX Spark", "N1X",
     'Lenovo - Yoga 9n 2-in-1 16" 3K OLED Touchscreen Laptop - NVIDIA RTX Spark N1X 20-Core ((2026)) - 64GB Memory',
     "nvidia-n1x-20c", "NVIDIA RTX Spark N1X 20-Core", "nvidia-n1x"),
]


@pytest.mark.parametrize("brand,series,number,title,model,name,cls", CPU_CASES,
                         ids=[f"{c[4]}:{c[2]}" for c in CPU_CASES])
def test_cpu_identity_name_and_class(brand, series, number, title, model, name, cls):
    cpu = N.parse_cpu(brand, series, number, title)
    assert (cpu.model, cpu.name, cpu.cls) == (model, name, cls)


@pytest.mark.parametrize("brand,series,number,title", [
    # no model number anywhere: stay unparsed rather than guess
    ("Intel", "Intel Core 5 Series 3", "Series 3",
     'HP - Omnibook 5 - 14” 2K OLED Touchscreen Laptop - Intel Core 5 Series 3 (2026) - 8GB Memory - 256GB SSD'),
    ("Snapdragon", "Qualcomm Snapdragon X Plus", None,
     'Microsoft - Surface Pro - Copilot+ PC - 12" Touchscreen - Snapdragon X Plus - 16GB Memory - 256GB UFS'),
    ("Intel", "Intel Core Ultra 5 Series 2", None,
     'Lenovo - Yoga 7i 2-in-1 - 16" 2K Touchscreen Laptop - Intel Core Ultra 5 Processor 2024 - 16GB Memory'),
    ("AMD", "AMD Ryzen 200 Series", None,
     'Dell - 15" 2K Touchscreen Laptop - AMD Ryzen 200 Series Ryzen 7 2026- 16GB Memory - 512GB Storage'),
])
def test_cpu_without_a_model_number_is_left_unparsed(brand, series, number, title):
    cpu = N.parse_cpu(brand, series, number, title)
    assert cpu.model is None and cpu.notes


@pytest.mark.parametrize("title", [
    # a year, a memory size, a product code and a model line named "Ultra" are not CPU model numbers
    'Lenovo - Yoga 7i 2-in-1 - 16" 2K Touchscreen Laptop - Intel Core Ultra 5 Processor 2024 - 16GB Memory - 512GB SSD',
    'Acer - TravelMate P2 15 TMP215-55-G2 15.6" IPS LCD 1920 x 1080(Full HD) Laptop-Intel Core Ultra 5-16GB Memory-512GB SSD Storage - Silver',
    'Lenovo - ThinkPad E16 Gen 3 AI PC 16" Laptop - Latest Model - Intel Ultra 5, 16GB Memory - 512GB SSD - Black',
    'HP - 15-FA1200NR 15.6" Laptop FHD Intel Core i5 With 8 GB DDR4 - 512GB SSD',
])
def test_title_numbers_that_are_not_cpu_models(title):
    cpu = N.parse_cpu("Intel", "Intel Core Ultra 5 Series 2", "226V", title)
    assert cpu.model == "intel-226v" and cpu.notes == []


def test_title_model_line_named_ultra_is_not_the_cpu():
    title = ('Samsung - Galaxy Book6 Ultra 16" Laptop - Intel Core Ultra X7 358H - Intel Arc B390 Graphics '
             'with 32GB Memory - 512GB SSD - Gray')
    cpu = N.parse_cpu("Intel", "Intel Core Ultra X7 Series 3", "Ultra X7 358H", title)
    assert (cpu.model, cpu.name, cpu.notes) == ("intel-358h", "Intel Core Ultra X7 358H", [])


def test_a_listing_that_names_two_cpus_is_not_grouped():
    cpu = N.parse_cpu("Intel", "Intel Core Ultra 5 Series 2", "226V",
                      'HP - 14" Laptop - Intel Core Ultra 5 228V - 16GB Memory')
    assert cpu.model is None
    assert any("226V" in n and "228V" in n for n in cpu.notes)


def test_one_model_number_sold_in_two_core_counts_is_two_cpus():
    """NVIDIA N1X: 18 and 20 cores share a model number and differ by $400 at Best Buy."""
    title = 'Microsoft - Surface Laptop Ultra - 15" 3K Touchscreen - NVIDIA RTX Spark N1X {}-Core (2026) - 32GB Memory'
    small = N.parse_cpu("NVIDIA", "NVIDIA RTX Spark", "N1X", title.format(18), "18-core")
    big = N.parse_cpu("NVIDIA", "NVIDIA RTX Spark", "N1X", title.format(20), "20-core")
    assert (small.model, big.model) == ("nvidia-n1x-18c", "nvidia-n1x-20c")
    assert small.cls == big.cls == "nvidia-n1x"
    assert N.cpu_class_label("nvidia-n1x") == "NVIDIA RTX Spark N1X"
    # Dell's title has no core count: the spec row alone decides
    dell = N.parse_cpu("NVIDIA", "NVIDIA RTX Spark", "N1X", 'Dell - XPS - 16" Laptop - NVIDIA RTX Spark N1X (2026)', "18-core")
    assert dell.model == "nvidia-n1x-18c"
    unknown = N.parse_cpu("NVIDIA", "NVIDIA RTX Spark", "N1X", "Dell - XPS 16 DX16263 - Graphite", None)
    contradictory = N.parse_cpu("NVIDIA", "NVIDIA RTX Spark", "N1X", title.format(18), "20-core")
    assert unknown.model is None and contradictory.model is None and unknown.cls == "nvidia-n1x"


@pytest.mark.parametrize("brand,series,number,title,model,cls", [
    # a maker's part number in the CPU field is not a CPU
    ("Snapdragon", "Snapdragon X2 Plus", "EP2-77800",
     'Microsoft - Surface Laptop Copilot+ PC 13" LCD Touchscreen Snapdragon X2 Plus Processor 16GB RAM', None, None),
    ("Snapdragon", "Snapdragon", "C Platform C1", 'HP - 14" Laptop - Snapdragon C Platform (2026)', "qualcomm-c1", "qualcomm-c1"),
    # "AI 2026" is a year, not "AI 2" + "026"
    ("AMD", "AMD Ryzen AI 400 Series", None, 'Dell - 14S - 14" Laptop - AMD Ryzen AI 400 Series AI 2026 - 16GB Memory', None, None),
    ("Intel", "Intel Core Ultra 200V Series", None, 'HP - 14" Laptop - Intel Core Ultra 200V Series - 16GB Memory', None, None),
    ("Intel", "Intel Processor U300", "U300", 'Acer - 15.6" Laptop - Intel Processor U300 - 8GB Memory', "intel-u300", "intel-u300"),
    ("AMD", "AMD Ryzen 9 9000 Series", "9955HX3D", 'ASUS - ROG Strix 16" Laptop - AMD Ryzen 9 9955HX3D - 32GB Memory',
     "amd-9955hx3d", "amd-ryzen-9-9000"),
    # generation of older Core i numbers: 8250U is 8th gen, 1135G7 is 11th
    ("Intel", "Intel 8th Generation Core i5", "8250U", 'HP - 15.6" Laptop - Intel Core i5-8250U - 8GB Memory',
     "intel-8250u", "intel-core-i5-g8"),
    ("Intel", "Intel 11th Generation Core i5", "1135G7", 'HP - 15.6" Laptop - Intel Core i5-1135G7 - 8GB Memory',
     "intel-1135g7", "intel-core-i5-g11"),
    # no "Processor Brand" row: "Kompanio Ultra 910" must not be read as an Intel Core Ultra 9
    (None, "MediaTek Kompanio Ultra Series", "910", 'Acer - Chromebook Plus Spin 514 14" - MediaTek Kompanio Ultra 910',
     "mediatek-910", "mediatek-910"),
])
def test_cpu_edge_cases(brand, series, number, title, model, cls):
    cpu = N.parse_cpu(brand, series, number, title)
    assert (cpu.model, cpu.cls) == (model, cls)


def test_near_miss_cpus_have_different_identities():
    def ident(brand, number):
        return N.parse_cpu(brand, None, number, None).model
    assert len({ident("Intel", "226V"), ident("Intel", "228V"), ident("Intel", "225U"), ident("Intel", "226")}) == 4
    assert ident("Intel", "350") != ident("AMD", "350")          # Core 7 350 vs Ryzen AI 7 350
    assert ident("Intel", "355") != ident("Intel", "N355")       # Core Ultra 7 355 vs Processor N355
    assert ident("AMD", "7535U") != ident("AMD", "7535HS")


# ---------------------------------------------------------------------------- GPU
@pytest.mark.parametrize("gtype,graphics,title,gpu", [
    ("Integrated", "Intel Arc", None, "integrated"),
    ("Integrated", "Intel Arc 130V", None, "integrated"),
    ("Integrated", "Intel Arc 140V", None, "integrated"),
    ("Integrated", "Intel Arc B390", None, "integrated"),
    ("Integrated", "Intel Graphics", None, "integrated"),
    ("Integrated, None", "Intel UHD Graphics 600", None, "integrated"),
    ("Integrated, Integrated", "AMD Radeon 840M", None, "integrated"),
    ("Integrated", "AMD Radeon 8060S", None, "integrated"),
    ("Integrated", "Qualcomm Adreno", None, "integrated"),
    ("Integrated", "Snapdragon X Elite Adreno", None, "integrated"),
    ("Integrated", "ARM Immortalis-G925'", None, "integrated"),
    ("Integrated", "MediaTek Integrated Graphics", None, "integrated"),
    ("Integrated", "NVIDIA Blackwell RTX", None, "integrated"),            # the N1X system-on-chip
    ("Dedicated", "Intel Arc", None, "integrated"),                        # Dell lists iGPUs as "Dedicated"
    ("Dedicated", "AMD Radeon 840M", None, "integrated"),
    ("Dedicated", "NVIDIA GeForce RTX 5060", None, "nvidia-rtx-5060"),
    ("Dedicated, Dedicated", "NVIDIA GeForce RTX 5070 Ti", None, "nvidia-rtx-5070-ti"),
    ("Dedicated", "NVIDIA RTX PRO 2000 Blackwell Generation", None, "nvidia-rtx-pro-2000"),
    ("Dedicated", "NVIDIA RTX A500", None, "nvidia-rtx-a500"),
    ("Dedicated", "NVIDIA GeForce RTX 4050", "MSI - Katana 15 - Ryzen 9-8945HS with 16GB Memory-RTX 4050-1TB SSD",
     "nvidia-rtx-4050"),
    # the spec table lists the iGPU, the title names the discrete card
    ("Integrated", "Intel Graphics",
     'Lenovo - Yoga Pro 7i 15.3" 3K OLED Laptop- Core Ultra 7 356H (2026)- NVIDIA GeForce RTX 5050- 32GB Memory',
     "nvidia-rtx-5050"),
    ("Dedicated", "Mystery Graphics 9000", None, None),
    (None, None, None, None),
])
def test_gpu(gtype, graphics, title, gpu):
    assert N.parse_gpu(gtype, graphics, title)[0] == gpu


def test_gpu_edge_cases():
    # two different graphics cards in one listing: not grouped on either
    gpu, _, notes = N.parse_gpu("Dedicated", "NVIDIA RTX A500", "Lenovo - ThinkPad P16s Gen 4 - NVIDIA RTX PRO500 - 32GB Memory")
    assert gpu is None and "rtx-a500" in notes[0] and "rtx-pro-500" in notes[0]
    # a trademark sign inside the name, the Ada generation, a discrete Radeon written without "RX"
    assert N.parse_gpu("Dedicated", "NVIDIA GeForce RTX™ 5070", None)[0] == "nvidia-rtx-5070"
    assert N.parse_gpu("Dedicated", "NVIDIA RTX 500 Ada Generation", None)[0] == "nvidia-rtx-500-ada"
    assert N.parse_gpu("Dedicated", "AMD Radeon 6600M", None)[0] is None
    assert N.parse_gpu("Dedicated", "AMD Radeon RX 6600M", None)[0] == "amd-rx-6600m"
    # the display name follows the card that was used
    gpu, name, _ = N.parse_gpu("Integrated", "Intel Graphics", 'Lenovo - Yoga Pro 7i 15.3" Laptop- NVIDIA GeForce RTX 5050- 32GB')
    assert (gpu, name) == ("nvidia-rtx-5050", "RTX 5050")
    assert N.parse_gpu("Integrated", "ARM Immortalis-G925'", None)[1] == "ARM Immortalis-G925"


def test_gpu_labels():
    assert N.gpu_label("nvidia-rtx-5070-ti") == "RTX 5070 Ti"
    assert N.gpu_label("nvidia-rtx-pro-2000") == "RTX PRO 2000"
    assert N.gpu_label("nvidia-rtx-a500") == "RTX A500"


# ---------------------------------------------------------------------------- capacities
@pytest.mark.parametrize("value,gb", [
    ("512 gigabytes", 512), ("1000 gigabytes", 1024), ("1024 gigabytes", 1024), ("2000 gigabytes", 2048),
    ("2048 gigabytes", 2048), ("4000 gigabytes", 4096), ("1 terabyte", 1024), ("256 gigabytes", 256),
    ("128 gigabytes", 128), ("64 gigabytes", 64), ("500 gigabytes", 512), ("1TB", 1024), (None, None),
])
def test_storage_is_snapped_to_one_size_per_drive(value, gb):
    assert N.snap_storage(N._gigabytes(value)) == gb


def test_capacity_edge_cases():
    assert N._gigabytes("1,024 gigabytes") == 1024                    # not 24
    # the title fallback never takes a graphics card's memory, or one drive of a pair, for the whole
    assert N._title_memory("MSI - Thin 15 - NVIDIA RTX 3050 6GB Memory - 16GB DDR5 - 512GB SSD") == 16
    assert N._title_memory("Lenovo - Yoga Pro 7i - NVIDIA GeForce RTX 5060- 32GB Memory- 1TB SSD") == 32
    assert N._title_memory("Samsung - Galaxy Book6 Pro - Intel Graphics with 16GB Memory - 512GB SSD") == 16
    assert N._title_storage("Lenovo - Upgraded - Yoga 7i 2-in-1, Ultra 5 226V, 16GB RAM, 512GB SSD+1TB Dock") == (None, None)
    assert N._title_storage("HP - 17.3 Laptop - 1TB HDD + 256GB SSD") == (None, None)
    assert N._title_storage("Dell - 15.6 Laptop - 16GB Memory - 512GB PCIe 4.0 SSD") == (512, "ssd")
    assert N._title_storage("Lenovo - IdeaPad Slim 3 - 8GB Memory - 256GB SSD - Blue") == (256, "ssd")


@pytest.mark.parametrize("value,kind", [
    ("SSD", "ssd"), ("SSD, SSD", "ssd"), ("eMMC", "emmc"), ("UFS", "ufs"), ("HDD", "hdd"),
    ("SSD, HDD", "hybrid"), ("Solid State Drive", "ssd"), (None, None), ("Punch cards", None),
])
def test_storage_type(value, kind):
    assert N.parse_storage_type(value) == kind


@pytest.mark.parametrize("value,title,expected", [
    ("Windows 11 Home", None, ("windows", "home")),
    ("Windows 11 Pro", None, ("windows", "pro")),
    ("Windows 11 Home in S Mode", None, ("windows", "s")),
    ("Chrome OS", None, ("chromeos", None)),
    ("Chrome Plus OS", None, ("chromeos", None)),
    ("Googlebook OS", None, ("googlebook", None)),
    ("macOS", None, ("macos", None)),
    (None, 'HP - 14" Chromebook - MediaTek Kompanio 540', ("chromeos", None)),
    (None, 'HP - 14" Laptop', (None, None)),
])
def test_os(value, title, expected):
    assert N.parse_os(value, title) == expected


# ---------------------------------------------------------------------------- chassis
@pytest.mark.parametrize("flag,title,form", [
    ("true", 'Lenovo - Yoga 7i 2-in-1 16" 2K Touchscreen Laptop', "convertible"),
    (None, 'Lenovo - ThinkPad T14s 2-in-1 AI PC 14" Touch - Screen Laptop', "convertible"),     # flag missing
    ("false", 'HP - Envy x360 15.6" Touchscreen Laptop', "convertible"),
    ("true", 'ASUS - Chromebook CM14 Flip - 14" 2K Touchscreen Laptop', "convertible"),
    ("true", 'Acer - TravelMate B3 Spin 12 TMB312R-31 2-in-1 12.2" Laptop', "convertible"),
    ("true", 'Dell - CC11260 11.6 N150 4G 64G CRM - Black', "convertible"),                     # only the flag says so
    ("false", 'Lenovo - Yoga Slim 7x 14" 2K OLED Touchscreen Laptop', "clamshell"),             # Yoga alone is no signal
    ("false", 'Lenovo - Yoga Pro 7i Aura Edition 15.3" 3K OLED Laptop', "clamshell"),
    (None, 'HP - 14" Laptop - Intel N150 Processor (2025)', "clamshell"),
    (None, 'Microsoft - Surface Pro - Copilot+ PC - 13" Touchscreen - Device with Keyboard', "detachable"),
    ("true", 'ASUS - ROG Flow Z13 13.4" 2.5K 180Hz Touch-Screen Gaming Laptop', "detachable"),
    ("true", 'ASUS - ProArt PZ14 14" 3K OLED Touchscreen Laptop', "detachable"),
])
def test_form_factor(flag, title, form):
    assert N.parse_form_factor(flag, title) == form


@pytest.mark.parametrize("flag,title,weight,form", [
    # two screens are neither a clamshell nor an ordinary 2-in-1
    ("false", 'ASUS - Zenbook Duo Dual 14" 3K OLED Touch Laptop - Evo Intel Core Ultra 9 285H', "3.64 pounds", "dual-screen"),
    ("true", 'Lenovo - Yoga Book 9i 2-in-1 14" 2.8K Dual Screen OLED Touchscreen Laptop', "2.948 pounds", "dual-screen"),
    # a "2-in-1" under two pounds is a tablet with a detachable keyboard
    ("true", 'ASUS - Chromebook CM32 - 12.1" 3K 2-in-1 Touchscreen Laptop - MediaTek Kompanio 500 Series', "1.41 pounds", "detachable"),
    ("true", 'Microsoft - 12" 2-in-1 Touchscreen', "24 ounces", "detachable"),
    ("true", 'Acer - Googlebook 14 Laptop - 14" 3K OLED 120Hz Touchscreen', "2.51 pounds", "convertible"),
    ("false", 'LG - gram 14" Laptop', "1.9 pounds", "clamshell"),                 # light, but not a 2-in-1
    # convertibles whose listing has no 2-in-1 flag and no "2-in-1" in the title
    (None, 'Samsung - Galaxy Book5 360 - Copilot+ PC - 15.6" FHD AMOLED Touch-Screen Laptop', None, "convertible"),
    (None, 'Samsung - Galaxy Book6 Pro 360 - 16" 3K AMOLED Touch Screen Laptop', None, "convertible"),
    (None, 'Lenovo - ThinkPad X1 Yoga Gen 8 14" Touch-Screen Laptop', None, "convertible"),
    (None, 'Lenovo - IdeaPad Flex 5i 14" 2K Touchscreen Laptop', None, "convertible"),
    (None, 'Lenovo - Yoga 7i Two-in-One 14" Touch-Screen Laptop', None, "convertible"),
    (None, 'Samsung - Galaxy Book5 Pro - 16" 3K AMOLED Touch-Screen Laptop', None, "clamshell"),
])
def test_form_factor_from_weight_and_model_words(flag, title, weight, form):
    assert N.parse_form_factor(flag, title, N.parse_weight_lb(weight)) == form


@pytest.mark.parametrize("flag,title,touch", [
    ("true", 'Dell - XPS 16 DX16263 - Graphite', True),
    ("false", 'HP - 17.3" Full HD Laptop', False),
    ("false", 'ASUS - Chromebook CM14 Flip - 14" 2K Touchscreen Laptop', True),        # the title is explicit
    (None, 'Lenovo - ThinkPad T14 Gen 5 14" Touch - Screen Laptop', True),
    (None, 'LG - gram 17" 2.5K Touch-Screen Laptop', True),
    ("false", 'HP - 15.6" Non-Touch Laptop with Precision Touchpad', False),
])
def test_touchscreen(flag, title, touch):
    assert N.parse_touch(flag, title, "clamshell")[0] is touch


def test_touch_unknown_stays_unknown_for_a_clamshell():
    assert N.parse_touch(None, 'HP - 14" Laptop', "clamshell")[0] is None
    assert N.parse_touch(None, 'HP - 14" Laptop', "convertible")[0] is True     # a 2-in-1 always has a touchscreen


# ---------------------------------------------------------------------------- reported fields
def test_screen_resolution_brand():
    assert N.parse_screen_size("15.6 inches", None) == 15.6
    assert N.parse_screen_size(None, 'HP - 14" Laptop') == 14.0
    assert N.parse_screen_size(None, 'ASUS - Vivobook 16 16” 2K Laptop') == 16.0
    assert [N.screen_bucket(size) for size in (11.6, 13.0, 13.3, 13.8, 14.0, 14.5, 15.0, 15.6, 16.0, 16.3, 17.0, 17.3, 18.0)] == \
        ["≤13"] * 4 + ["14"] * 2 + ["15–16"] * 4 + ["17+"] * 3
    assert N.screen_bucket(None) is None and N.SCREEN_BUCKETS == ("≤13", "14", "15–16", "17+")
    assert N.parse_resolution("1920 x 1200 (WUXGA)") == "1920x1200"
    assert N.parse_resolution("1900 x 1200 (WUXGA)") == "1920x1200"      # a known data-entry typo
    assert N.parse_resolution("2880 x 1800") == "2880x1800"
    assert N.canonical_brand("HP OMEN") == "HP" and N.canonical_brand("Alienware") == "Dell"
    assert N.canonical_brand("Lenovo") == "Lenovo" and N.canonical_brand(None) is None


@pytest.mark.parametrize("title,brand,family", [
    ('Lenovo - Yoga 7i 2-in-1 - Copilot+ PC - 16" 2K Touchscreen Laptop - Intel Core Ultra 5', "Lenovo", "Yoga 7i 2-in-1"),
    ('HP - OmniBook X Flip 2-in-1 - 14" 2K Touchscreen Laptop', "HP", "OmniBook X Flip 2-in-1"),
    ('LG - gram Pro 16" 144Hz 2.5K Laptop', "LG", "gram Pro"),
    ('HP - 14" Laptop - Intel N150 Processor (2025)', "HP", None),
    # "Laptop" and "Gaming" are part of some product names
    ('Microsoft - Surface Laptop - 13.8" 2K Touchscreen - Snapdragon X2 Elite', "Microsoft", "Surface Laptop"),
    ('Microsoft - Surface Laptop Ultra - 15" 3K Touchscreen', "Microsoft", "Surface Laptop Ultra"),
    ('ASUS - TUF Gaming A16 16" FHD+ 165Hz Gaming Laptop', "ASUS", "TUF Gaming A16"),
    ('Acer - Aspire Go 15 Laptop - 15.6" Full HD IPS Display', "Acer", "Aspire Go 15"),
    # sub-brands listed as their own brand keep their name
    ('HP OMEN - MAX 16" 240Hz Gaming Laptop - Intel Core Ultra 9 275HX', "HP OMEN", "OMEN MAX"),
    ('Alienware - Area-51 18" 3K Gaming Laptop', "Alienware", "Alienware Area-51"),
    # titles with no inch mark stop at the first spec
    ("Dell - CC11260 11.6 N150 4G 64G CRM - Black", "Dell", "CC11260"),
    ("Dell - Pro Max 16 U7 32G 1T W11P - Magnetite, Other", "Dell", "Pro Max 16"),
    ("ASUS - Vivobook 16 WUXGA Laptop - AMD Ryzen 5 150", "ASUS", "Vivobook 16"),
    ("Dell - XPS 16 DX16263 - Graphite", "Dell", "XPS 16 DX16263"),
])
def test_model_family(title, brand, family):
    assert N.parse_model_family(title, brand) == family


# ---------------------------------------------------------------------------- whole products
def test_full_product_gets_a_key_at_every_level():
    p = make_product('HP - OmniBook X Flip 2-in-1 - 14" 2K Touchscreen Laptop - Intel Core Ultra 5 226V (2024) - '
                     '16GB Memory - 512GB SSD - Copilot+ - Atmospheric Blue', "HP",
                     cpu_brand="Intel", series="Intel Core Ultra 5 Series 2", number="226V", ram="16 gigabytes",
                     storage="512 gigabytes", storage_type="SSD", graphics="Intel Arc 130V",
                     graphics_type="Integrated", os=WIN, touch="true", two_in_one="true",
                     size="14 inches", resolution="1920 x 1200 (WUXGA)")
    s = N.normalise(p)
    assert (s.brand, s.cpu_model, s.memory_gb, s.storage_gb, s.storage_type, s.form_factor, s.touchscreen,
            s.os, s.os_edition, s.gpu, s.gpu_name, s.screen_size_in, s.resolution) == \
           ("HP", "intel-226v", 16, 512, "ssd", "convertible", True, "windows", "home", "integrated",
            "Intel Arc 130V", 14.0, "1920x1200")
    assert s.strict_key and s.group_key and s.relaxed_key and s.class_key and not s.needs_review
    assert len({s.strict_key, s.group_key, s.relaxed_key, s.class_key}) == 4 and s.screen_bucket == "14"
    assert N.normalise(p).to_dict() == s.to_dict()                     # deterministic


def test_missing_key_field_means_review_not_a_guess():
    p = make_product('Microsoft - Surface Pro - Copilot+ PC - 12" Touchscreen - Snapdragon X Plus - 16GB Memory - '
                     '256GB UFS - Device Only - Platinum', "Microsoft", cpu_brand="Snapdragon",
                     series="Qualcomm Snapdragon X Plus", ram="16 gigabytes", storage="256 gigabytes", os=WIN)
    s = N.normalise(p)
    assert s.needs_review and s.group_key is None
    assert s.cpu_model is None and s.gpu is None
    assert (s.memory_gb, s.storage_gb, s.storage_type, s.form_factor, s.touchscreen) == (16, 256, "ufs", "detachable", True)


def test_title_only_listing_is_normalised_from_the_title():
    p = make_product('Lenovo - IdeaPad Slim 3 15.6" Full HD Laptop - AMD Ryzen 5 7520U - 8GB Memory - 256GB SSD - Blue',
                     "Lenovo")
    s = N.normalise(p)
    assert (s.cpu_model, s.memory_gb, s.storage_gb, s.storage_type, s.form_factor, s.screen_size_in) == \
           ("amd-7520u", 8, 256, "ssd", "clamshell", 15.6)
    assert s.needs_review                # no OS, touch or graphics stated: not grouped on a guess


def test_colour_size_and_os_edition_do_not_split_a_group():
    def variant(title, **kw):
        base = dict(cpu_brand="Intel", series="Intel Core Ultra 5 Series 2", number="226V", ram="16 gigabytes",
                    storage="512 gigabytes", storage_type="SSD", graphics="Intel Arc", graphics_type="Integrated",
                    os=WIN, touch="true", two_in_one="true", size="14 inches")
        base.update(kw)
        return N.normalise(make_product(title, "HP", **base))
    a = variant('HP - OmniBook X Flip 2-in-1 - 14" Touchscreen Laptop - Atmospheric Blue')
    b = variant('HP - OmniBook X Flip 2-in-1 - 16" Touchscreen Laptop - Meteor Silver', size="16 inches",
                os="Windows 11 Pro", graphics="Intel Arc 130V", storage="500 gigabytes")
    assert a.group_key == b.group_key
    assert a.strict_key != b.strict_key                                # only the strict level looks at the screen


def test_labels():
    spec = {"cpu_class": "intel-core-ultra-5-s2", "memory_gb": 16, "storage_gb": 1024, "storage_type": "ssd",
            "form_factor": "convertible", "touchscreen": True, "os": "windows", "gpu": "integrated",
            "screen_bucket": "≤13"}
    assert N.group_label(spec, "Intel Core Ultra 5 226V") == \
        "Core Ultra 5 226V · 16 GB · 1 TB SSD · Convertible touch · Windows"
    assert N.group_label(spec, "Intel Core Ultra 5 226V", "strict") == \
        'Core Ultra 5 226V · 16 GB · 1 TB SSD · Convertible touch · ≤13" · Windows'
    assert N.group_label(spec, "Intel Core Ultra 5 226V", "relaxed") == \
        "Core Ultra 5 226V · 16 GB · 1 TB · Convertible · Windows"
    assert N.group_label(spec, "Intel Core Ultra 5 226V", "class") == \
        "Core Ultra 5 Series 2 · 16 GB · 1 TB · Convertible · Windows"
    gaming = dict(spec, gpu="nvidia-rtx-5060", form_factor="clamshell", touchscreen=False)
    assert N.group_label(gaming, "AMD Ryzen 7 250") == \
        "AMD Ryzen 7 250 · RTX 5060 · 16 GB · 1 TB SSD · Clamshell non-touch · Windows"
