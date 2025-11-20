import time
from auto_simulator import AutoSimulator
from drive_simulator import DriveSimulator, get_route_coordinates, get_route_coords, load_serbian_roads

import math
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box
import pygeohash
from datetime import time as dt_time, timedelta

POGLED_UNAPRED_KM = 2.0
VREMENSKI_OPSEG_SATI = 1
VREMENSKI_OPSEG_DANA = 30
GODINA_ZA_ANALIZУ = 2021
GEOHASH_PRECISION = 7


class AccidentWarningSystem:

    def __init__(self, putanja_do_fajla, tip_indeksa='geohash'):
        print("Inicijalizacija sistema sa GeoHash (prostor) i Datetime (vreme) indeksima...")
        self.gdf_nezgode = self._ucitaj_i_pripremi_podatke(putanja_do_fajla)
        self.indeks = self._izgradi_indeks(tip_indeksa)
        if self.indeks is None:
            raise Exception("Indeksi nisu uspešno izgrađeni. Prekidam rad.")
        print("Sistem je spreman.")

    def _ucitaj_i_pripremi_podatke(self, putanja_do_fajla):
        print(f"Učitavanje i obrada podataka iz: {putanja_do_fajla}")
        try:
            df = pd.read_excel(putanja_do_fajla, header=None)
            df.rename(columns={3: 'datum', 4: 'lon', 5: 'lat'}, inplace=True)
        except FileNotFoundError:
            print(f"GREŠKA: Fajl nije pronađen na putanji: {putanja_do_fajla}")
            return None

        df['vreme_nezgode'] = pd.to_datetime(df['datum'], errors='coerce', format='%d.%m.%Y,%H:%M')
        df = df.dropna(subset=['vreme_nezgode', 'lon', 'lat'])
        df = df[df['vreme_nezgode'].dt.year == GODINA_ZA_ANALIZУ].copy()

        geometry = [Point(xy) for xy in zip(df['lon'], df['lat'])]
        gdf = gpd.GeoDataFrame(df, geometry=geometry, crs='EPSG:4326')

        print(f"Izračunavanje GeoHash-eva sa preciznošću {GEOHASH_PRECISION}...")
        gdf['geohash'] = gdf.apply(
            lambda row: pygeohash.encode(row.geometry.y, row.geometry.x, precision=GEOHASH_PRECISION),
            axis=1
        )

        print("Kreiranje i sortiranje vremenskog indeksa (DatetimeIndex)...")
        gdf.set_index('vreme_nezgode', inplace=True)
        gdf.sort_index(inplace=True)

        print(f"Obrada završena. Učitano {len(gdf)} nezgoda za {GODINA_ZA_ANALIZУ}. godinu.")
        return gdf

    def _izgradi_indeks(self, tip_indeksa):
        if self.gdf_nezgode is None:
            return None
        if tip_indeksa == 'geohash' and isinstance(self.gdf_nezgode.index, pd.DatetimeIndex):
            print("Prostorni (GeoHash) i vremenski (DatetimeIndex) indeksi su uspešno pripremljeni.")
            return self.gdf_nezgode
        else:
            print("GREŠКА: Indeksi nisu pravilno konfigurisani.")
            return None

    def _definisi_oblast_pretrage(self, trenutna_tacka):
        lat_stepen_u_km = 111.1
        lon_stepen_u_km = lat_stepen_u_km * math.cos(math.radians(trenutna_tacka.y))
        offset_lat = POGLED_UNAPRED_KM / lat_stepen_u_km
        offset_lon = POGLED_UNAPRED_KM / lon_stepen_u_km
        lon, lat = trenutna_tacka.x, trenutna_tacka.y
        return box(lon - offset_lon, lat - offset_lat, lon + offset_lon, lat + offset_lat)

    def proveri_opasnosti_na_deonici(self, trenutna_lokacija, trenutno_vreme):
        oblast_pretrage = self._definisi_oblast_pretrage(trenutna_lokacija)
        bbox = oblast_pretrage.bounds
        bounding_box_obj = pygeohash.BoundingBox(bbox[1], bbox[0], bbox[3], bbox[2])
        geohashes_to_check = pygeohash.geohashes_in_box(bounding_box_obj, precision=GEOHASH_PRECISION)

        if not geohashes_to_check:
            return 0, 0, 0

        ids_kandidata = self.indeks[self.indeks['geohash'].str.startswith(tuple(geohashes_to_check))]

        if ids_kandidata.empty:
            return 0, 0, 0

        nezgode_u_oblasti = ids_kandidata[ids_kandidata.intersects(oblast_pretrage)]
        broj_ukupno = len(nezgode_u_oblasti)

        if broj_ukupno == 0:
            return 0, 0, 0


        start_date = trenutno_vreme - timedelta(days=VREMENSKI_OPSEG_DANA)
        end_date = trenutno_vreme + timedelta(days=VREMENSKI_OPSEG_DANA)

        nezgode_sezona = nezgode_u_oblasti.loc[start_date:end_date]
        broj_doba_godine = len(nezgode_sezona)

        start_time = (trenutno_vreme - timedelta(hours=VREMENSKI_OPSEG_SATI)).time()
        end_time = (trenutno_vreme + timedelta(hours=VREMENSKI_OPSEG_SATI)).time()

        nezgode_dan = nezgode_u_oblasti.between_time(start_time, end_time)
        broj_doba_dana = len(nezgode_dan)

        return broj_ukupno, broj_doba_dana, broj_doba_godine

    @staticmethod
    def klasifikuj_opasnost(ukupno, doba_dana, doba_godine):
        skor = (ukupno * 1) + (doba_godine * 1.5) + (doba_dana * 2)
        if skor > 15:
            return "VEOMA OPASNO"
        elif skor > 8:
            return "OPASNO"
        elif skor > 2:
            return "UMERENO OPASNO"
        else:
            return "Bezbedno"


sistem_upozorenja = None


def load_accidents_data():
    global sistem_upozorenja
    putanja_do_fajla_nezgoda = 'dataset/nez-opendata-2021-20220125.xlsx'
    try:
        sistem_upozorenja = AccidentWarningSystem(putanja_do_fajla_nezgoda, tip_indeksa='geohash')
    except Exception as e:
        print(f"FATALNA GREŠKA: Sistem za upozorenje nije mogao biti pokrenut. Greška: {e}")
        sistem_upozorenja = None


if __name__ == "__main__":
    load_accidents_data()
    if sistem_upozorenja is None:
        exit()

    start_city = "Beograd"
    end_city = "Novi Sad"

    G = load_serbian_roads()
    print(f"Ucitana mreža puteva Srbije! {len(G.nodes)} čvorova, {len(G.edges)} ivica.")
    orig, dest = get_route_coordinates(start_city, end_city)
    route_coords, route = get_route_coords(G, orig, dest, start_city, end_city)

    if route_coords is None:
        print("Završavam program jer ruta nije pronađena.")
        exit()

    drive_simulator = DriveSimulator(G, edge_color='lightgray', edge_linewidth=0.5)
    drive_simulator.prikazi_mapu(route_coords, route_color='blue', auto_marker_color='ro', auto_marker_size=8)
    automobil = AutoSimulator(route_coords, speed_kmh=250, interval=1.0)
    automobil.running = True


    def on_close(event):
        print("\n=== Zaustavljanje simulacije... ===")
        automobil.running = False


    drive_simulator.fig.canvas.mpl_connect('close_event', on_close)

    print("\n=== Simulacija pokrenuta ===")
    print("Kontrole: Auto se pomera automatski svakih", automobil.interval, "sekundi")
    print("Za zaustavljanje pritisnite Ctrl+C ili zatvorite prozor sa mapom.\n")

    vreme_polaska = pd.Timestamp("2021-07-15 14:00:00")
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

                    trenutno_vreme_simulacije = vreme_polaska + timedelta(seconds=step_count * automobil.interval)

                    ukupno, doba_dana, doba_godine = sistem_upozorenja.proveri_opasnosti_na_deonici(
                        trenutna_lokacija_point, trenutno_vreme_simulacije)

                    nivo_opasnosti = sistem_upozorenja.klasifikuj_opasnost(ukupno, doba_dana, doba_godine)

                    print(f"Vreme: {trenutno_vreme_simulacije.time()} | Lokacija ({lat:.4f}, {lon:.4f}) | "
                          f"Analiza: U={ukupno}, DD={doba_dana}, DG={doba_godine} | NIVO OPASNOSTI: {nivo_opasnosti}")

            if automobil.is_finished():
                print("\n=== Automobil je stigao na destinaciju! ===")
                break

            time.sleep(interval_simulacije)

    except KeyboardInterrupt:
        print("\n\n=== Simulacija je prekinuta (Ctrl+C) ===")

    if automobil.running:
        drive_simulator.finish_drive()

    print("=== Kraj programa ===")