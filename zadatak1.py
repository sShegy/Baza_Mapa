# -*- coding: utf-8 -*-
"""
Poboljšana i optimizovana verzija koda za Projekat 1a - Primena prostornih indeksa.
Ovaj kod služi kao referenca za proveru i poređenje rešenja.

Ključne karakteristike:
- Objektno-orijentisan pristup (sva logika je u klasi).
- Optimizovano učitavanje podataka sa unapred izračunatim vremenskim kolonama.
- Modularan dizajn za laku promenu tipa indeksa.
- Čista i jasna struktura za lakše razumevanje.
"""
import math

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box
from rtree import index
import time

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
            # KORAK 1: Učitavamo fajl i kažemo mu da NEMA ZAGLAVLJE (header=None)
            df = pd.read_excel(putanja_do_fajla, header=None)

            # KORAK 2: Dodeljujemo imena kolonama koje nas interesuju
            # Kolone se broje od 0. Na osnovu slike:
            # Kolona D je 3. kolona (datum)
            # Kolona E je 4. kolona (longituda)
            # Kolona F je 5. kolona (latituda)
            df.rename(columns={
                3: 'datum',
                4: 'lon',
                5: 'lat'
            }, inplace=True)

        except FileNotFoundError:
            print(f"GREŠKA: Fajl nije pronađen na putanji: {putanja_do_fajla}")
            return None

        # Odavde kod ostaje isti, jer smo uspešno preimenovali kolone
        # Konverzija u datetime i filtriranje samo jedne godine
        # Dodajemo i format da pomognemo pandasu da razume "DD.MM.YYYY,HH:MM"
        df['vreme_nezgode'] = pd.to_datetime(df['datum'], errors='coerce', format='%d.%m.%Y,%H:%M')
        df = df.dropna(subset=['vreme_nezgode', 'lon', 'lat'])
        df = df[df['vreme_nezgode'].dt.year == GODINA_ZA_ANALIZU].copy()

        # OPTIMIZACIJA: Unapred izračunavamo vremenske komponente
        df['sat'] = df['vreme_nezgode'].dt.hour
        df['dan_u_godini'] = df['vreme_nezgode'].dt.dayofyear

        # Kreiranje GeoDataFrame-a
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

        # Modularni pristup za različite indekse
        if tip_indeksa == 'rtree':
            idx = index.Index()
            # .itertuples() je brži od .iterrows()
            for red in self.gdf_nezgode.itertuples():
                idx.insert(red.Index, red.geometry.bounds)
            print("R-tree indeks uspešno izgrađen.")
            return idx

        elif tip_indeksa == 'geohash':
            # Ovde bi išla implementacija za GeoHash. Primer:
            # import pygeohash
            # self.gdf_nezgode['geohash'] = self.gdf_nezgode.apply(
            #     lambda r: pygeohash.encode(r.geometry.y, r.geometry.x, precision=7), axis=1
            # )
            # return self.gdf_nezgode.set_index('geohash') # Vraća GeoDataFrame sa indeksom
            print("GeoHash još uvek nije implementiran u ovom primeru.")
            return None  # Placeholder

        else:
            print(f"GREŠKA: Nepodržan tip indeksa '{tip_indeksa}'")
            return None

    def _definisi_oblast_pretrage(self, trenutna_tacka):
        """
        Definiše pravougaonu oblast (bounding box) ispred i oko vozila.
        """
        # Aproksimacija: 1 stepen latitude ≈ 111.1 km
        # 1 stepen longitude ≈ 111.1 km * cos(latitude)
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

        # 1. Brzi prostorni upit pomoću indeksa
        ids_kandidata = list(self.indeks.intersection(oblast_pretrage.bounds))
        if not ids_kandidata:
            return 0, 0, 0

        nezgode_u_oblasti = self.gdf_nezgode.iloc[ids_kandidata]
        # Fina provera preseka (jer R-tree vraća kandidate iz pravougaonika)
        nezgode_u_oblasti = nezgode_u_oblasti[nezgode_u_oblasti.intersects(oblast_pretrage)]

        broj_ukupno = len(nezgode_u_oblasti)
        if broj_ukupno == 0:
            return 0, 0, 0

        # 2. Vremenski upit - doba dana (koristimo pre-kalkulisanu kolonu 'sat')
        sat = trenutno_vreme.hour
        donja_granica_sati, gornja_granica_sati = (sat - VREMENSKI_OPSEG_SATI), (sat + VREMENSKI_OPSEG_SATI)
        broj_doba_dana = len(
            nezgode_u_oblasti[nezgode_u_oblasti['sat'].between(donja_granica_sati, gornja_granica_sati)])

        # 3. Vremenski upit - doba godine (koristimo pre-kalkulisanu kolonu 'dan_u_godini')
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
        # Ponderisani skor - veći značaj dajemo relevantnijim nezgodama
        skor = (ukupno * 1) + (doba_godine * 1.5) + (doba_dana * 2)

        if skor > 15:
            return "VEOMA OPASNO"
        elif skor > 8:
            return "OPASNO"
        elif skor > 2:
            return "UMERENO OPASNO"
        else:
            return "Bezbedno"


def main_simulation():
    """
    Glavna funkcija za pokretanje simulacije.
    """
    # !! STUDENT TREBA DA ZAMENI OVO SVOJOM PUTANJOM DO FAJLA !!
    putanja_do_fajla = 'nez-opendata-2021-20220125.xlsx'

    try:
        # Kreiramo instancu našeg sistema
        sistem_upozorenja = AccidentWarningSystem(putanja_do_fajla, tip_indeksa='rtree')
    except Exception as e:
        print(f"Došlo je do greške pri inicijalizaciji: {e}")
        return

    # --- OVDE SE INTEGRŠE VAŠ KOD ZA SIMULACIJU KRETANJA ---
    # `ruta_voznje` treba da dođe iz vašeg `kolokvijum1_spatial.py` fajla.
    # Ovo je samo primer.
    ruta_voznje = [
        Point(20.45, 44.81), Point(20.40, 44.85), Point(20.35, 44.90),
        Point(20.25, 45.00), Point(20.10, 45.15), Point(19.83, 45.26)
    ]

    print("\n--- Početak simulacije vožnje ---")
    for i, tacka in enumerate(ruta_voznje):
        # Dobijanje trenutne lokacije i vremena iz simulacije
        trenutna_lokacija = tacka
        trenutno_vreme = pd.Timestamp.now()  # U pravoj simulaciji, ovo vreme bi se takođe menjalo

        # Pozivamo naš sistem da izvrši proveru
        u, dd, dg = sistem_upozorenja.proveri_opasnosti_na_deonici(trenutna_lokacija, trenutno_vreme)

        # Klasifikujemo i ispisujemo rezultat
        nivo_opasnosti = sistem_upozorenja.klasifikuj_opasnost(u, dd, dg)

        print(f"\nKorak {i + 1}: Lokacija ({tacka.y:.4f}, {tacka.x:.4f})")
        print(f"Analiza deonice -> Ukupno: {u}, U isto doba dana: {dd}, U isto doba godine: {dg}")
        print(f"NIVO OPASNOSTI: {nivo_opasnosti}")

        time.sleep(1)  # Pauza radi preglednosti

    print("\n--- Simulacija završena ---")


if __name__ == '__main__':
    main_simulation()