# -*- coding: utf-8 -*-
import time
from auto_simulator import AutoSimulator
from drive_simulator import DriveSimulator, get_route_coordinates, get_route_coords, load_serbian_roads, \
    show_route_distances

# --- IZMENJENI IMPORTI ZA SISTEM UPOZORENJA ---
import math
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box
# Uklonjen je 'rtree', dodat je 'pygeohash'
import pygeohash

# -------------------------------------------


# =============================================================================
# === KLASA ZA ANALIZU I UPOZORAVANJE OD OPASNOSTI (GeoHash verzija) ===
# =============================================================================

# Konstante za lakše podešavanje
POGLED_UNAPRED_KM = 2.0
VREMENSKI_OPSEG_SATI = 1
VREMENSKI_OPSEG_DANA = 30
GODINA_ZA_ANALIZU = 2021
GEOHASH_PRECISION = 7  # Dobar balans: ćelije su ~153x153 metra


class AccidentWarningSystem:
    """
    Enkapsulira svu logiku za učitavanje podataka, izgradnju indeksa
    i proveru opasnosti na putu KORISTEĆI GEOHASH.
    """

    def __init__(self, putanja_do_fajla, tip_indeksa='geohash'):
        """
        Inicijalizuje sistem, učitava podatke i priprema ih za GeoHash upite.
        """
        print("Inicijalizacija sistema za upozorenje sa GeoHash-om...")
        self.gdf_nezgode = self._ucitaj_i_pripremi_podatke(putanja_do_fajla)
        self.indeks = self._izgradi_indeks(tip_indeksa)
        if self.indeks is None:
            raise Exception("Podaci za GeoHash nisu uspešno pripremljeni. Prekidam rad.")
        print("Sistem je spreman.")

    def _ucitaj_i_pripremi_podatke(self, putanja_do_fajla):
        """
        Privatna metoda za učitavanje i temeljna pripremu podataka sa GeoHash-om.
        """
        print(f"Učitavanje i obrada podataka iz: {putanja_do_fajla}")
        try:
            df = pd.read_excel(putanja_do_fajla, header=None)
            df.rename(columns={3: 'datum', 4: 'lon', 5: 'lat'}, inplace=True)
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

        print(f"Izračunavanje GeoHash-eva sa preciznošću {GEOHASH_PRECISION}...")
        gdf['geohash'] = gdf.apply(
            lambda row: pygeohash.encode(row.geometry.y, row.geometry.x, precision=GEOHASH_PRECISION),
            axis=1
        )

        print(f"Obrada završena. Učitano {len(gdf)} nezgoda za {GODINA_ZA_ANALIZU}. godinu.")
        return gdf

    def _izgradi_indeks(self, tip_indeksa):
        """
        Za GeoHash, "Indeks" je sama GeoDataFrame tabela sa 'geohash' kolonom.
        """
        if self.gdf_nezgode is None:
            return None
        if tip_indeksa == 'geohash':
            if 'geohash' in self.gdf_nezgode.columns:
                print("GeoHash podaci su uspešno pripremljeni.")
                return self.gdf_nezgode
            else:
                print("GREŠKA: Kolona 'geohash' nije pronađena.")
                return None
        else:
            print(f"GREŠKA: Ovaj sistem je konfigurisan samo za 'geohash', a ne za '{tip_indeksa}'")
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
        Glavna javna metoda koja vrši sve provere koristeći GeoHash.
        """
        oblast_pretrage = self._definisi_oblast_pretrage(trenutna_lokacija)

        # --- FINALNA ISPRAVKA ---
        bbox = oblast_pretrage.bounds  # tuple: (min_lon, min_lat, max_lon, max_lat)
        # BoundingBox prima pozicione argumente redosledom: (south, west, north, east)
        # south = min_lat = bbox[1]
        # west = min_lon = bbox[0]
        # north = max_lat = bbox[3]
        # east = max_lon = bbox[2]
        bounding_box_obj = pygeohash.BoundingBox(bbox[1], bbox[0], bbox[3], bbox[2])
        geohashes_to_check = pygeohash.geohashes_in_box(bounding_box_obj)
        # ---------------------------

        if not geohashes_to_check:
            return 0, 0, 0

        ids_kandidata = self.indeks[self.indeks['geohash'].str.startswith(tuple(geohashes_to_check))]

        if ids_kandidata.empty:
            return 0, 0, 0

        nezgode_u_oblasti = ids_kandidata[ids_kandidata.intersects(oblast_pretrage)]
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
        Statička metoda za klasifikaciju nivoa opasnosti.
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

sistem_upozorenja = None


def load_accidents_data():
    global sistem_upozorenja
    putanja_do_fajla_nezgoda = '../nez-opendata-2021-20220125.xlsx'
    try:
        sistem_upozorenja = AccidentWarningSystem(putanja_do_fajla_nezgoda, tip_indeksa='geohash')
    except Exception as e:
        print(f"FATALNA GREŠKA: Sistem za upozorenje nije mogao biti pokrenut. Greška: {e}")
        sistem_upozorenja = None


def check_accident_zone(lat, lon):
    pass


if __name__ == "__main__":
    load_accidents_data()
    if sistem_upozorenja is None:
        exit()

    start_city = "Pančevo"
    end_city = "Zrenjanin"

    G = load_serbian_roads()
    print(f"Ucitana mreža puteva Srbije! {len(G.nodes)} čvorova, {len(G.edges)} ivica.")
    orig, dest = get_route_coordinates(start_city, end_city)
    route_coords, route = get_route_coords(G, orig, dest)
    drive_simulator = DriveSimulator(G, edge_color='lightgray', edge_linewidth=0.5)
    drive_simulator.prikazi_mapu(route_coords, route_color='blue', auto_marker_color='ro', auto_marker_size=8)
    automobil = AutoSimulator(route_coords, speed_kmh=250, interval=1.0)
    automobil.running = True

    print("\n=== Simulacija pokrenuta ===")
    print("Kontrole: Auto se pomera automatski svakih", automobil.interval, "sekundi")
    print("Za zaustavljanje pritisnite Ctrl+C\n")

    interval_simulacije = 1.0
    try:
        step_count = 0
        while automobil.running:
            auto_current_pos = automobil.move()
            lat, lon = auto_current_pos
            drive_simulator.move_auto_marker(lat, lon, automobil.get_progress_info(), plot_pause=0.01)
            step_count += 1
            if step_count % 5 == 0:
                if sistem_upozorenja:
                    trenutna_lokacija_point = Point(lon, lat)
                    trenutno_vreme = pd.Timestamp.now()
                    ukupno, doba_dana, doba_godine = sistem_upozorenja.proveri_opasnosti_na_deonici(
                        trenutna_lokacija_point, trenutno_vreme)
                    nivo_opasnosti = sistem_upozorenja.klasifikuj_opasnost(ukupno, doba_dana, doba_godine)
                    print(
                        f"Lokacija ({lat:.4f}, {lon:.4f}) | Analiza: U={ukupno}, DD={doba_dana}, DG={doba_godine} | NIVO OPASNOSTI: {nivo_opasnosti}")

            if automobil.is_finished():
                print("\n=== Automobil je stigao na destinaciju! ===")
                break
            time.sleep(interval_simulacije)
    except KeyboardInterrupt:
        print("\n\n=== Simulacija prekinuta ===")
    drive_simulator.finish_drive()