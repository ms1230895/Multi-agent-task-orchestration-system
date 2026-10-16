"""Hand-written right and wrong answers for the 30 real tasks (data for AC6).

`SAMPLES` maps a task id to `((good_answer, good_files), (bad_answer, bad_files))`. Each
`files` dict is what the agent would have written into the workspace (path -> content).
"""

Sample = tuple[str, dict[str, str]]

SAMPLES: dict[str, tuple[Sample, Sample]] = {
    "calc-01": (("The result is 11,626.", {}), ("It is 11626000", {})),
    "calc-02": (("sqrt(7056) = 84", {}), ("It is 83", {})),
    "calc-03": (("13107", {}), ("13106.8", {})),
    "calc-04": (("About 7401.22", {}), ("It is 7400", {})),
    "calc-05": (("15% of 2480 is 372.", {}), ("It is 37.2", {})),
    "calc-06": (("The remainder is 48.", {}), ("It is 47", {})),
    "calc-07": (("ln(1000) ≈ 6.9078", {}), ("It is 6.9", {})),
    "calc-08": (("The area is 90 m².", {}), ("The area is 19.7", {})),
    "search-01": (("1989", {}), ("1961", {})),
    "search-02": (("Gold has atomic number 79.", {}), ("It is 47", {})),
    "search-03": (("Michelangelo painted it.", {}), ("Leonardo da Vinci", {})),
    "search-04": (("The capital is Canberra.", {}), ("Sydney", {})),
    "search-05": (("It was released in 1991.", {}), ("It was 1994", {})),
    "search-06": (("299,792,458 m/s", {}), ("300000 m/s", {})),
    "search-07": (("Gabriel García Márquez", {}), ("Isabel Allende", {})),
    "file-01": (
        ("Done.", {"status.txt": "benchmark ok\n"}),
        ("Done.", {"status.txt": "'benchmark ok'"}),
    ),
    "file-02": (("The third line says harbor.", {}), ("The third line says violet.", {})),
    "file-03": (("There are 7 non-empty lines.", {}), ("There are 8 lines.", {})),
    "file-04": (
        ("Done.", {"upper.txt": "ADA LOVELACE\n"}),
        ("Done.", {"upper.txt": "ada lovelace"}),
    ),
    "file-05": (
        ("Done.", {"shopping.txt": "eggs\nmilk\nbread\n"}),
        ("Done.", {"shopping.txt": "eggs, milk, bread"}),
    ),
    "file-06": (("The server uses port 8443.", {}), ("Port 80", {})),
    "file-07": (
        ("Done.", {"reports/today.txt": "nested ok"}),
        ("Done.", {"today.txt": "nested ok"}),
    ),
    "multi-01": (("Done.", {"eiffel.txt": "111"}), ("Done.", {"eiffel.txt": "112"})),
    "multi-02": (("The average is 25.", {}), ("The average is 125", {})),
    "multi-03": (("Done.", {"power.txt": "1,048,576"}), ("Done.", {"power.txt": "1024"})),
    "multi-04": (("6 * 8 = 48", {}), ("6 * 8 = 49", {})),
    "multi-05": (("Done.", {"total.txt": "8.45"}), ("Done.", {"total.txt": "8.5"})),
    "multi-06": (
        ("Done.", {"moon.txt": "Neil Armstrong\n"}),
        ("Done.", {"moon.txt": "Buzz Aldrin"}),
    ),
    "multi-07": (
        ("Done.", {"light.txt": "17,987,547,480"}),
        ("Done.", {"light.txt": "17987547"}),
    ),
    "multi-08": (("Done.", {"capital.txt": "Tokyo\n"}), ("Done.", {"capital.txt": "Kyoto"})),
}
