# -*- coding: utf-8 -*-
import time
from auto_simulator import AutoSimulator
from drive_simulator import DriveSimulator, get_route_coordinates, get_route_coords, load_serbian_roads, \
    show_route_distances

# --- DODATI IMPORTI ZA SISTEM UPOZORENJA ---
import math
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box
from rtree import index

# -------------------------------------------


# =============================================================================
# === KLASA ZA ANALIZU I UPOZORAVANJE OD OPASNOSTI (VAŠ KOD) ===
# =============================================================================

# Konstante za lakše podešavanje
POGLED_UNAPRED_KM = 2.0  # Koliko kilometara unapred gledamo
VREMENSKI_OPSEG_SATI = 1  # +/- 1 sat za doba dana
VREMENSKI_OPSEG_DANA = 30  # +/- 30 dana za doba godine
GODINA_ZA_ANALIZU = 2021  # Prema zadatku, dovoljno je uzeti jednu godinu


class AccidentWarningSystem:
    """
    Enkapsulira svu logiku za učitavanje podataka, izgradnju indeksa
    i proveru opasnosti na putu.
    """

    def __init__(self, putanja_do_fajla, tip_indeksa='rtree'):
        """
        Inicijalizuje sistem, učitava podatke i gradi odgovarajući indeks.
        """
        print("Inicijalizacija sistema za upozorenje...")
        self.gdf_nezgode = self._ucitaj_i_pripremi_podatke(putanja_do_fajla)
        self.indeks = self._izgradi_indeks(tip_indeksa)
        if self.indeks is None:
            raise Exception("Indeks nije uspešno izgrađen. Prekidam rad.")
        print("Sistem je spreman.")

    def _ucitaj_i_pripremi_podatke(self, putanja_do_fajla):
        """
        Privatna metoda za učitavanje i temeljna pripremu podataka.
        """
        print(f"Učitavanje i obrada podataka iz: {putanja_do_fajla}")
        try:
            df = pd.read_excel(putanja_do_fajla, header=None)
            df.rename(columns={
                3: 'datum',
                4: 'lon',
                5: 'lat'
            }, inplace=True)

        except FileNotFoundError:
            print(f"GREŠKA: Fajl nije pronađen na putanji: {putanja_do_fajla}")
            return None

        df['vreme_nezgode'] = pd.to_datetime(df['datum'], errors='coerce', format='%d.%m.%Y,%H:%M')
        df = df.dropna(subset=['vreme_nezgode', 'lon', 'lat'])
        df = df[df['vreme_nezgode'].dt.year == GODINA_ZA_ANALIZU].copy()

        df['sat'] = df['vreme_nezgode'].dt.hour
        df['dan_u_godini'] = df['vreme_nezgode'].dt.dayofyear

        geometry = [Point(xy) for xy in zip(df['lon'], df['lat'])]
        gdf = gpd.GeoDataFrame(df, geometry=geometry, crs='EPSG:4326')

        print(f"Obrada završena. Učitano {len(gdf)} nezgoda za {GODINA_ZA_ANALIZU}. godinu.")
        return gdf

    def _izgradi_indeks(self, tip_indeksa):
        """
        Gradi prostorni indeks na osnovu izabranog tipa.
        """
        if self.gdf_nezgode is None:
            return None

        print(f"Izgradnja '{tip_indeksa}' indeksa...")
        if tip_indeksa == 'rtree':
            idx = index.Index()
            for red in self.gdf_nezgode.itertuples():
                idx.insert(red.Index, red.geometry.bounds)
            print("R-tree indeks uspešno izgrađen.")
            return idx
        else:
            print(f"GREŠKA: Nepodržan tip indeksa '{tip_indeksa}'")
            return None

    def _definisi_oblast_pretrage(self, trenutna_tacka):
        """
        Definiše pravougaonu oblast (bounding box) ispred i oko vozila.
        """
        lat_stepen_u_km = 111.1
        lon_stepen_u_km = lat_stepen_u_km * math.cos(math.radians(trenutna_tacka.y))
        offset_lat = POGLED_UNAPRED_KM / lat_stepen_u_km
        offset_lon = POGLED_UNAPRED_KM / lon_stepen_u_km
        lon, lat = trenutna_tacka.x, trenutna_tacka.y
        return box(lon - offset_lon, lat - offset_lat, lon + offset_lon, lat + offset_lat)

    def proveri_opasnosti_na_deonici(self, trenutna_lokacija, trenutno_vreme):
        """
        Glavna javna metoda koja vrši sve provere za datu lokaciju i vreme.
        """
        oblast_pretrage = self._definisi_oblast_pretrage(trenutna_lokacija)
        ids_kandidata = list(self.indeks.intersection(oblast_pretrage.bounds))
        if not ids_kandidata:
            return 0, 0, 0

        nezgode_u_oblasti = self.gdf_nezgode.iloc[ids_kandidata]
        nezgode_u_oblasti = nezgode_u_oblasti[nezgode_u_oblasti.intersects(oblast_pretrage)]
        broj_ukupno = len(nezgode_u_oblasti)
        if broj_ukupno == 0:
            return 0, 0, 0

        sat = trenutno_vreme.hour
        donja_granica_sati, gornja_granica_sati = (sat - VREMENSKI_OPSEG_SATI), (sat + VREMENSKI_OPSEG_SATI)
        broj_doba_dana = len(
            nezgode_u_oblasti[nezgode_u_oblasti['sat'].between(donja_granica_sati, gornja_granica_sati)])

        dan = trenutno_vreme.dayofyear
        donja_granica_dana, gornja_granica_dana = (dan - VREMENSKI_OPSEG_DANA), (dan + VREMENSKI_OPSEG_DANA)
        broj_doba_godine = len(
            nezgode_u_oblasti[nezgode_u_oblasti['dan_u_godini'].between(donja_granica_dana, gornja_granica_dana)])

        return broj_ukupno, broj_doba_dana, broj_doba_godine

    @staticmethod
    def klasifikuj_opasnost(ukupno, doba_dana, doba_godine):
        """
        Statička metoda za klasifikaciju nivoa opasnosti. Ne zavisi od stanja objekta.
        """
        skor = (ukupno * 1) + (doba_godine * 1.5) + (doba_dana * 2)
        if skor > 15:
            return "VEOMA OPASNO"
        elif skor > 8:
            return "OPASNO"
        elif skor > 2:
            return "UMERENO OPASNO"
        else:
            return "Bezbedno"


# =============================================================================
# === GLAVNI DEO SIMULACIJE ===
# =============================================================================

# Globalna promenljiva za naš sistem upozorenja
sistem_upozorenja = None


def load_accidents_data():
    """
    Inicijalizuje AccidentWarningSystem i učitava podatke o nezgodama.
    """
    global sistem_upozorenja

    # Putanja do fajla je relativna u odnosu na lokaciju ovog .py fajla
    # '../' znači 'idi jedan folder gore'
    putanja_do_fajla_nezgoda = '../nez-opendata-2021-20220125.xlsx'

    try:
        sistem_upozorenja = AccidentWarningSystem(putanja_do_fajla_nezgoda)
    except Exception as e:
        print(f"FATALNA GREŠKA: Sistem za upozorenje nije mogao biti pokrenut. Greška: {e}")
        sistem_upozorenja = None


def check_accident_zone(lat, lon):
    """
    Ova funkcija se sada ne koristi, sva logika je prebačena direktno u glavnu petlju.
    """
    pass


if __name__ == "__main__":

    # ------------------------------
    # 1. Učitaj podatke o nezgodama koristeći AccidentWarningSystem
    # ------------------------------
    load_accidents_data()
    # -------------------------------
    # -------------------------------

    start_city = "Beograd"
    end_city = "Novi Sad"

    # 2. Učitaj mrežu puteva Srbije
    G = load_serbian_roads()
    print(f"Ucitana mreža puteva Srbije! {len(G.nodes)} čvorova, {len(G.edges)} ivica.")

    # 3. Odredjivanje koordinata pocetka i kraja rute
    orig, dest = get_route_coordinates(start_city, end_city)

    # 4. Odredjivanje rute
    route_coords, route = get_route_coords(G, orig, dest)

    # 5. Inicijalizacija grafičke mape za voznju rutom
    drive_simulator = DriveSimulator(G, edge_color='lightgray', edge_linewidth=0.5)

    # 6. Prikaz mape sa rutom
    drive_simulator.prikazi_mapu(route_coords, route_color='blue', auto_marker_color='ro', auto_marker_size=8)

    # 7. Inicijalizuj simulator kretanja automobila sa brzinom 250 km/h i intervalom od 1 sekunde  
    automobil = AutoSimulator(route_coords, speed_kmh=250, interval=1.0)
    automobil.running = True

    print("\n=== Simulacija pokrenuta ===")
    print("Kontrole: Auto se pomera automatski svakih", automobil.interval, "sekundi")
    print("Za zaustavljanje pritisnite Ctrl+C\n")

    interval_simulacije = 1.0  # sekunde
    # 8. Glavna petlja simulacije
    try:
        step_count = 0
        while automobil.running:
            # Pomeri automobil
            auto_current_pos = automobil.move()
            lat, lon = auto_current_pos

            drive_simulator.move_auto_marker(lat, lon, automobil.get_progress_info(), plot_pause=0.01)

            # Pozovi proveru okoline samo na svakih 5 koraka (da ne zatrpava konzolu)
            step_count += 1
            if step_count % 5 == 0:
                # -------------------------------------------------------------------------
                # --- INTEGRACIJA SA SISTEMOM UPOZORENJA ---
                # -------------------------------------------------------------------------
                if sistem_upozorenja:
                    # Kreiramo Shapely Point objekat od trenutnih koordinata
                    trenutna_lokacija_point = Point(lon, lat)
                    trenutno_vreme = pd.Timestamp.now()

                    # Pozivamo metode iz vaše klase
                    ukupno, doba_dana, doba_godine = sistem_upozorenja.proveri_opasnosti_na_deonici(
                        trenutna_lokacija_point, trenutno_vreme)
                    nivo_opasnosti = sistem_upozorenja.klasifikuj_opasnost(ukupno, doba_dana, doba_godine)

                    # Ispisujemo rezultat u terminal
                    print(
                        f"Lokacija ({lat:.4f}, {lon:.4f}) | Analiza: U={ukupno}, DD={doba_dana}, DG={doba_godine} | NIVO OPASNOSTI: {nivo_opasnosti}")

                else:
                    # Ova poruka će se ispisivati ako učitavanje podataka nije uspelo
                    print("Sistem za upozorenje nije inicijalizovan. Ne mogu proveriti opasnosti.")
                # -------------------------------------------------------------------------

            # Proveri da li je stigao na kraj
            if automobil.is_finished():
                print("\n=== Automobil je stigao na destinaciju! ===")
                break

            # Čekaj interval pre sledećeg pomeraja
            time.sleep(interval_simulacije)

    except KeyboardInterrupt:
        print("\n\n=== Simulacija prekinuta ===")

    drive_simulator.finish_drive()